const blue = (data, index) => {
  const r = data[index]; const g = data[index + 1]; const b = data[index + 2];
  return b > 135 && b - r > 60 && b - g > 4;
};

export function blueButtonRects(imageData, width, height) {
  // Connected blue borders/fills, not global image fractions: padding and long
  // mobile captures must not change which cells count as selected.
  const visited = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  const rects = [];
  for (let start = 0; start < width * height; start++) {
    if (visited[start] || !blue(imageData,start * 4)) continue;
    let head = 0, tail = 1, left = width, right = 0, top = height, bottom = 0;
    queue[0] = start; visited[start] = 1;
    while (head < tail) {
      const p = queue[head++], x = p % width, y = Math.floor(p / width);
      left = Math.min(left,x); right = Math.max(right,x); top = Math.min(top,y); bottom = Math.max(bottom,y);
      for (const next of [x ? p-1 : -1,x+1 < width ? p+1 : -1,y ? p-width : -1,y+1 < height ? p+width : -1]) {
        if (next < 0 || visited[next] || !blue(imageData,next*4)) continue;
        visited[next] = 1; queue[tail++] = next;
      }
    }
    const w = right-left+1, h = bottom-top+1;
    if (w < 24 || h < 14 || w/h < 1.15 || w/h > 7 || tail < 2*(w+h)*.65) continue;
    const edge=(horizontal,fixed,from,to)=> {
      let hits=0;
      for(let moving=from;moving<=to;moving++) if(blue(imageData,((horizontal ? fixed*width+moving : moving*width+fixed))*4)) hits++;
      return hits/(to-from+1);
    };
    const borderCoverage=(horizontal,start,step,from,to)=>Math.max(...[0,1,2,3].map(offset=>edge(horizontal,start+step*offset,from,to)));
    if(borderCoverage(true,top,1,left,right)<.6 || borderCoverage(true,bottom,-1,left,right)<.6 || borderCoverage(false,left,1,top,bottom)<.6 || borderCoverage(false,right,-1,top,bottom)<.6) continue;
    rects.push({left,top,width:w,height:h});
  }
  return rects.sort((a,b) => a.top-b.top || a.left-b.left);
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

export function buttonChoiceIndex(text, options = []) {
  const value = String(text || "").replace(/\s+/g, "").toLowerCase();
  const aliases = (choice) => {
    const selected = String(choice || "");
    if (/오버/.test(selected)) return ["오버", "over", "o"];
    if (/언더/.test(selected)) return ["언더", "under", "u"];
    if (/무/.test(selected)) return ["무", "draw"];
    if (/원정|패/.test(selected)) return ["패", "원정", "away"];
    if (/홈|승/.test(selected)) return ["승", "홈", "home"];
    return [selected];
  };
  const candidates = options.map((option, index) => ({ index,
    hit: aliases(option?.["선택"]).some((alias) => alias && (alias.length === 1 && /[ou]/i.test(alias)
      ? new RegExp(`^${alias}(?=[0-9.]|$)`, "i").test(value) : value.includes(alias.toLowerCase()))) }))
    .filter((row) => row.hit);
  return candidates.length === 1 ? candidates[0].index : null;
}
