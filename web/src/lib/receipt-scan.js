import { blueButtonRects, buttonOdds } from "./receipt-image.js";

// All recognition runs locally. Image adapters keep the same pipeline testable in Node.
export async function scanReceiptImage(
  file,
  { worker, decode, encode, onProgress = () => {} },
) {
  const original = await decode(file);
  const { width, height, data } = original;
  if (!width || !height || width * height > 24000000)
    throw new Error("이미지는 2,400만 화소 이하로 잘라서 넣어 주세요.");
  const rects = blueButtonRects(data, width, height);
  if (rects.length > 40)
    throw new Error("선택 영역이 너무 많습니다. 한 투표지만 잘라 넣어 주세요.");
  if (
    rects.some(
      (rect, index) =>
        index && rect.top < rects[index - 1].top + rects[index - 1].height,
    )
  )
    throw new Error(
      "같은 행에 선택 영역이 여러 개 있습니다. 한 투표지의 선택 내역만 보이도록 잘라 주세요.",
    );
  const headerRows = [];
  for (let y = 0; y < height; y++) {
    let left = width,
      right = 0,
      count = 0;
    for (let x = 0; x < width; x++) {
      const p = (y * width + x) * 4,
        r = data[p],
        g = data[p + 1],
        b = data[p + 2];
      if (
        r < 50 ||
        r > 140 ||
        g < 55 ||
        g > 155 ||
        b < 110 ||
        b > 200 ||
        b - r < 15 ||
        b - r > 65
      )
        continue;
      left = Math.min(left, x);
      right = x;
      count++;
    }
    if (count > 200 && count > (right - left) * 0.8)
      headerRows.push({ y, left, width: right - left + 1 });
  }
  const table = headerRows
    .filter((row) => row.y < (rects[0]?.top ?? 0))
    .at(-1) || { left: 0, width, y: 0 };
  const contentTop =
    headerRows.find(
      (row) =>
        row.y < table.y &&
        Math.abs(row.left - table.left) < 3 &&
        Math.abs(row.width - table.width) < 3,
    )?.y || 0;
  let contentBottom = height - 1;
  if (rects.length)
    outer: for (; contentBottom > rects.at(-1).top; contentBottom--) {
      for (let x = table.left; x < table.left + table.width; x++) {
        const p = (contentBottom * width + x) * 4;
        if (data[p] < 235 || data[p + 1] < 235 || data[p + 2] < 235)
          break outer;
      }
    }
  const contentHeight = contentBottom - contentTop + 1;
  const normalHeight =
    rects.map((rect) => rect.height).sort((a, b) => a - b)[
      Math.floor(rects.length / 2)
    ] || 40;
  const scale = Math.min(
    4,
    120 / normalHeight,
    4200 / Math.max(table.width, contentHeight),
    Math.sqrt(6000000 / (table.width * contentHeight)),
  );
  const prepare = (rect, invert = false, contrast = false) => {
    const w = Math.max(1, Math.round(rect.width * scale)),
      h = Math.max(1, Math.round(rect.height * scale));
    const pixels = new Uint8ClampedArray((w + 24) * (h + 24) * 4).fill(255);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const sx = rect.left + x / scale,
          sy = rect.top + y / scale;
        const ix = Math.floor(sx),
          iy = Math.floor(sy),
          fx = sx - ix,
          fy = sy - iy;
        const luminance = (xx, yy) => {
          const p =
            (Math.min(height - 1, yy) * width + Math.min(width - 1, xx)) * 4;
          const a = data[p + 3] / 255;
          return (
            (0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2]) * a +
            255 * (1 - a)
          );
        };
        const light =
          luminance(ix, iy) * (1 - fx) * (1 - fy) +
          luminance(ix + 1, iy) * fx * (1 - fy) +
          luminance(ix, iy + 1) * (1 - fx) * fy +
          luminance(ix + 1, iy + 1) * fx * fy;
        const value = invert
          ? light > 195
            ? 0
            : 255
          : contrast
            ? Math.max(0, Math.min(255, (light - 100) * 2))
            : Math.round(light);
        const target = ((y + 12) * (w + 24) + x + 12) * 4;
        pixels[target] = pixels[target + 1] = pixels[target + 2] = value;
      }
    return encode({ data: pixels, width: w + 24, height: h + 24 });
  };
  await worker.setParameters({
    tessedit_pageseg_mode: "6",
    preserve_interword_spaces: "1",
  });
  const full = await worker.recognize(
    await prepare({
      left: table.left,
      top: contentTop,
      width: table.width,
      height: contentHeight,
    }),
  );
  onProgress(25);
  const rows = [];
  for (let i = 0; i < rects.length; i++) {
    const rect = rects[i];
    let dark = 0,
      total = 0;
    for (let y = rect.top + 3; y < rect.top + rect.height - 3; y += 2)
      for (let x = rect.left + 3; x < rect.left + rect.width - 3; x += 2) {
        const p = (y * width + x) * 4;
        total++;
        if (data[p] < 80 && data[p + 2] > 100) dark++;
      }
    await worker.setParameters({ tessedit_pageseg_mode: "6" });
    const button = await worker.recognize(
      await prepare(
        {
          left: rect.left + Math.round(rect.width * 0.18),
          top: rect.top + 3,
          width: Math.round(rect.width * 0.64),
          height: rect.height - 6,
        },
        dark > total * 0.45,
      ),
    );
    // A row crop associates the selected box with its own game, never another OCR row.
    const previousBottom = i ? rects[i - 1].top + rects[i - 1].height : 0;
    const top = Math.max(
      table.y + 1,
      previousBottom + 1,
      Math.round(rect.top - rect.height * 1.55),
    );
    const rowRect = {
      left: table.left,
      top,
      width: table.width,
      height: rect.top + rect.height - top + 3,
    };
    await worker.setParameters({ tessedit_pageseg_mode: "6" });
    const row = await worker.recognize(await prepare(rowRect));
    await worker.setParameters({ tessedit_pageseg_mode: "11" });
    const detail = await worker.recognize(await prepare(rowRect, false, true));
    const number = await worker.recognize(
      await prepare(
        {
          left: table.left,
          top: rect.top,
          width: Math.round((rect.left - table.left) * 0.3),
          height: rect.height,
        },
        false,
        true,
      ),
    );
    const modern = /조합|한경기/.test(row.data.text + detail.data.text);
    const teamLeft =
      table.left + Math.round(table.width * (modern ? 0.29 : 0.34));
    const teamRect = modern
      ? {
          left: teamLeft,
          top: Math.max(top, Math.round(rect.top - rect.height * 1.18)),
          width: Math.round(table.width * 0.61),
          height: Math.round(rect.height * 0.68),
        }
      : {
          left: teamLeft,
          top: Math.round(rect.top + rect.height * 0.12),
          width: Math.max(1, rect.left - teamLeft - 3),
          height: Math.round(rect.height * 0.55),
        };
    await worker.setParameters({ tessedit_pageseg_mode: "7" });
    const teams = await worker.recognize(await prepare(teamRect));
    const greenRuns = [];
    for (let y = rect.top; y < rect.top + rect.height; y++) {
      let start = -1;
      for (let x = table.left; x <= rect.left; x++) {
        const p = (y * width + x) * 4,
          r = data[p],
          g = data[p + 1],
          b = data[p + 2];
        const green =
          x < rect.left &&
          g > 245 &&
          r > 210 &&
          Math.abs(r - b) < 5 &&
          g - r > 10 &&
          g - b > 10;
        if (green && start < 0) start = x;
        if (!green && start >= 0) {
          if (x - start > 20) greenRuns.push({ left: start, right: x - 1, y });
          start = -1;
        }
      }
    }
    const widest = greenRuns.sort(
      (a, b) => b.right - b.left - (a.right - a.left),
    )[0];
    const band = widest
      ? greenRuns.filter(
          (run) =>
            Math.abs(run.left - widest.left) < normalHeight * 0.1 &&
            Math.abs(run.right - widest.right) < normalHeight * 0.1,
        )
      : [];
    const greenLeft = widest?.left || 0,
      greenRight = widest?.right || 0;
    const greenTop = Math.min(...band.map((run) => run.y)),
      greenBottom = Math.max(...band.map((run) => run.y));
    let lineText = "";
    if (
      greenRight - greenLeft > 20 &&
      greenRight - greenLeft < table.width * 0.3 &&
      greenBottom > greenTop
    ) {
      const lineCrop = await worker.recognize(
        await prepare({
          left: greenLeft,
          top: greenTop,
          width: greenRight - greenLeft + 1,
          height: greenBottom - greenTop + 1,
        }),
      );
      lineText = lineCrop.data.text;
    }
    rows.push({
      text: row.data.text,
      detailText: detail.data.text,
      numberText: number.data.text,
      teamText: teams.data.text,
      lineText,
      buttonText: button.data.text,
      purchaseOdds: buttonOdds(button.data.text),
      rect,
    });
    onProgress(25 + Math.round((70 * (i + 1)) / rects.length));
  }
  let summaryText = "";
  const lastBottom = rects.at(-1)
    ? rects.at(-1).top + rects.at(-1).height
    : height;
  const summaryHeader = headerRows.find((row) => row.y > lastBottom);
  if (summaryHeader) {
    const bottom = headerRows
      .filter(
        (row) => row.y < summaryHeader.y + (rects.at(-1)?.height || 40) * 1.3,
      )
      .at(-1).y;
    const summaryTop = bottom + 1;
    await worker.setParameters({ tessedit_pageseg_mode: "6" });
    const summary = await worker.recognize(
      await prepare({
        left: table.left,
        top: summaryTop,
        width: table.width,
        height: Math.min(
          height - summaryTop,
          Math.round((bottom - summaryHeader.y + 1) * 1.5),
        ),
      }),
    );
    summaryText = summary.data.text;
  }
  return { text: full.data.text, summaryText, rows, width, height };
}

export async function decodeBrowserImage(file) {
  let image;
  if (typeof createImageBitmap === "function")
    image = await createImageBitmap(file);
  else {
    const url = URL.createObjectURL(file);
    try {
      image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () =>
          reject(new Error("지원하지 않는 이미지 형식입니다."));
        img.src = url;
      });
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  try {
    const width = image.width,
      height = image.height;
    if (width * height > 24000000)
      throw new Error("이미지는 2,400만 화소 이하로 잘라서 넣어 주세요.");
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.fillStyle = "white";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0);
    return context.getImageData(0, 0, width, height);
  } finally {
    image.close?.();
  }
}

export function encodeBrowserImage({ data, width, height }) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas
    .getContext("2d")
    .putImageData(new ImageData(data, width, height), 0, 0);
  return canvas;
}
