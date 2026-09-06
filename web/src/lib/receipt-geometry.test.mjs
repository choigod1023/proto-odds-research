import test from "node:test";
import assert from "node:assert/strict";
import { blueButtonRects } from "./receipt-image.js";

// Synthetic RGBA only; no screenshots, browser, OCR worker, or private files.
// Keep this import relative for integration with the parent's updated detector.
const MODERN_BLUE = [36, 112, 244, 255];
const DARK_BLUE = [24, 67, 148, 255];
const WHITE = [255, 255, 255, 255];
const HEADER = [100, 120, 150, 255];

function canvas(width = 360, height = 240) {
  const data = new Uint8ClampedArray(width * height * 4).fill(255);
  return { width, height, data };
}

function fill(image, rect, color) {
  assert.ok(rect.left >= 0 && rect.top >= 0);
  assert.ok(rect.left + rect.width <= image.width && rect.top + rect.height <= image.height);
  for (let y = rect.top; y < rect.top + rect.height; y++) {
    for (let x = rect.left; x < rect.left + rect.width; x++) {
      image.data.set(color, (y * image.width + x) * 4);
    }
  }
}

function border(image, rect, color = MODERN_BLUE) {
  fill(image, rect, color);
  fill(image, { left: rect.left + 1, top: rect.top + 1, width: rect.width - 2, height: rect.height - 2 }, WHITE);
}

function detect(image) {
  return blueButtonRects(image.data, image.width, image.height);
}

test("modern one-pixel blue outline yields the exact cell bounds", () => {
  const image = canvas();
  const rect = { left: 160, top: 70, width: 96, height: 28 };
  border(image, rect);
  assert.deepEqual(detect(image), [rect]);
});

test("dark filled selection remains one box despite white text holes", () => {
  const image = canvas();
  const rect = { left: 180, top: 85, width: 92, height: 30 };
  fill(image, rect, DARK_BLUE);
  for (const left of [195, 211, 227, 243]) {
    fill(image, { left, top: 94, width: 6, height: 12 }, WHITE);
  }
  assert.deepEqual(detect(image), [rect]);
});

test("white side padding and long mobile canvas do not change selected bounds", () => {
  const rect = { left: 32, top: 44, width: 80, height: 24 };
  const compact = canvas(180, 140);
  border(compact, rect);
  const padded = canvas(620, 1600);
  const translated = { ...rect, left: rect.left + 210, top: rect.top + 650 };
  border(padded, translated);
  assert.deepEqual(detect(compact), [rect]);
  assert.deepEqual(detect(padded), [translated]);
});

test("vertically separated cells with a four-pixel gap must never merge", () => {
  const image = canvas(340, 900);
  const rects = [
    { left: 190, top: 310, width: 92, height: 28 },
    { left: 190, top: 342, width: 92, height: 28 },
    { left: 190, top: 650, width: 92, height: 28 },
  ];
  rects.forEach((rect) => border(image, rect));
  assert.deepEqual(detect(image), rects);
});

test("same-row cells remain separate and sorted; downstream must group them by row", () => {
  const image = canvas();
  const rects = [
    { left: 80, top: 80, width: 60, height: 24 },
    { left: 160, top: 80, width: 60, height: 24 },
  ];
  // Reverse paint order must not define row order.
  [...rects].reverse().forEach((rect) => border(image, rect));
  assert.deepEqual(detect(image), rects);
});

test("slate table header, wide blue rule, and isolated blue noise are not selections", () => {
  const image = canvas(480, 300);
  fill(image, { left: 15, top: 10, width: 450, height: 30 }, HEADER);
  fill(image, { left: 15, top: 50, width: 450, height: 2 }, MODERN_BLUE);
  for (let index = 0; index < 20; index++) {
    fill(image, { left: 20 + index * 20, top: 75, width: 3, height: 5 }, MODERN_BLUE);
  }
  const rect = { left: 310, top: 180, width: 96, height: 28 };
  border(image, rect);
  assert.deepEqual(detect(image), [rect]);
});

test("connected noisy header glyphs without an enclosing border must not become a selected cell", () => {
  const image = canvas(480, 300);
  // A blue horizontal rule joins narrow header glyphs into one component. Its bounding
  // box and pixel count look like a button, but most of its perimeter is absent.
  fill(image, { left: 30, top: 20, width: 120, height: 1 }, MODERN_BLUE);
  for (let left = 30; left < 150; left += 4) {
    fill(image, { left, top: 20, width: 1, height: 28 }, MODERN_BLUE);
  }
  const selected = { left: 310, top: 180, width: 96, height: 28 };
  border(image, selected);
  assert.deepEqual(detect(image), [selected], "header noise must not add a phantom OCR row or move the table anchor");
});

test("empty white image produces no candidate selections", () => {
  assert.deepEqual(detect(canvas()), []);
});
