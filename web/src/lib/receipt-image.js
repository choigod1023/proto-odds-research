const blue = (data, index) => {
  const r = data[index]; const g = data[index + 1]; const b = data[index + 2];
  return b > 135 && b - r > 22 && b - g > 4;
};

export function blueButtonRects(imageData, width, height) {
  const runs = [];
  const minWidth = width * 0.18;
  for (let y = 0; y < height; y += 1) {
    let first = -1; let last = -1; let count = 0;
    for (let x = 0; x < width; x += 1) {
      if (!blue(imageData, (y * width + x) * 4)) continue;
      if (first < 0) first = x;
      last = x; count += 1;
    }
    if (count >= minWidth && last - first >= minWidth && last - first <= width * 0.62) runs.push({ y, left: first, right: last });
  }
  const rects = [];
  for (let topIndex = 0; topIndex < runs.length; topIndex += 1) {
    const top = runs[topIndex];
    const bottom = runs.slice(topIndex + 1).find((row) => row.y - top.y >= height * 0.018
      && row.y - top.y <= height * 0.09 && Math.abs(row.left - top.left) < width * 0.03
      && Math.abs(row.right - top.right) < width * 0.03);
    if (!bottom) continue;
    const rect = { left: Math.min(top.left, bottom.left), top: top.y,
      width: Math.max(top.right, bottom.right) - Math.min(top.left, bottom.left) + 1,
      height: bottom.y - top.y + 1 };
    if (!rects.some((other) => Math.abs(other.top - rect.top) < height * 0.02)) rects.push(rect);
  }
  return rects.sort((a, b) => a.top - b.top).reduce((merged, rect) => {
    const previous = merged.at(-1);
    const sameColumn = previous && Math.abs(previous.left - rect.left) < width * 0.03
      && Math.abs(previous.width - rect.width) < width * 0.05;
    if (sameColumn && rect.top <= previous.top + previous.height + height * 0.015) {
      previous.height = Math.max(previous.top + previous.height, rect.top + rect.height) - previous.top;
    } else merged.push({ ...rect });
    return merged;
  }, []);
}

export async function selectedButtonRects(file) {
  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width; canvas.height = bitmap.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
  return { rects: blueButtonRects(pixels.data, canvas.width, canvas.height), width: canvas.width, height: canvas.height };
}

export function visualChoiceIndex(rect, optionCount, imageWidth) {
  if (!rect || optionCount < 2) return null;
  return Math.max(0, Math.min(optionCount - 1, Math.floor(((rect.left + rect.width / 2) / imageWidth) * optionCount)));
}

export function buttonOdds(text) {
  const normalized = String(text || "").replace(",", ".");
  const decimal = normalized.match(/(?:^|\D)(\d{1,2}\.\d{1,2})(?=\D|$)/)?.[1];
  if (decimal) return Number(decimal);
  const squeezed = normalized.match(/(?:^|\D)(1\d{2})(?=\D|$)/)?.[1];
  return squeezed ? Number(`${squeezed[0]}.${squeezed.slice(1)}`) : null;
}
