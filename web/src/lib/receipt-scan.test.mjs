import test from "node:test";
import assert from "node:assert/strict";
import { scanReceiptImage } from "./receipt-scan.js";

function raster(rects, width = 600, height = 1000) {
  const data = new Uint8ClampedArray(width * height * 4).fill(255);
  for (const { x, y, w = 80, h = 24 } of rects)
    for (let yy = y; yy < y + h; yy++)
      for (let xx = x; xx < x + w; xx++) {
        if (xx === x || xx === x + w - 1 || yy === y || yy === y + h - 1)
          data.set([0, 140, 255, 255], (yy * width + xx) * 4);
      }
  return { data, width, height };
}
test("excessive candidate boxes stop before OCR calls", async () => {
  let calls = 0;
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () =>
        raster(
          Array.from({ length: 50 }, (_, i) => ({ x: 100, y: i * 35 })),
          600,
          2000,
        ),
      encode: async (v) => v,
      worker: {
        recognize: () => {
          calls++;
        },
      },
    }),
    /너무 많/,
  );
  assert.equal(calls, 0);
});
test("same-row multiple selections fail closed instead of pairing another row's identity", async () => {
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () =>
        raster([
          { x: 100, y: 60 },
          { x: 300, y: 60 },
        ]),
      worker: {},
      encode: (v) => v,
    }),
    /같은 행/,
  );
});
test("oversized decoded images stop before allocating selection masks", async () => {
  await assert.rejects(
    scanReceiptImage(null, {
      decode: async () => ({ width: 10000, height: 10000, data: [] }),
      worker: {},
    }),
    /화소/,
  );
});
test("no colored selection does not fall back to current odds or screen position", async () => {
  const result = await scanReceiptImage(null, {
    decode: async () => raster([], 100, 100),
    encode: (v) => v,
    worker: {
      setParameters: async () => {},
      recognize: async () => ({ data: { text: "게임번호 9 승 1.91" } }),
    },
  });
  assert.deepEqual(result.rows, []);
});
