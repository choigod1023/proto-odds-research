import test from "node:test";
import assert from "node:assert/strict";
import { buttonChoiceIndex, receiptInkRect, normalizeReceiptRaster } from "./receipt-image.js";

const threeWay = [{ "선택": "핸디홈" }, { "선택": "핸디무" }, { "선택": "핸디원정" }];

test("선택 버튼 OCR 글자로 데스크톱 3지선다 픽을 판별한다", () => {
  assert.equal(buttonChoiceIndex("승 1.51", threeWay), 0);
  assert.equal(buttonChoiceIndex("패 1.79", threeWay), 2);
});

test("언더오버 버튼도 위치가 아니라 글자로 판별한다", () => {
  assert.equal(buttonChoiceIndex("오버 1.75", [{ "선택": "언더" }, { "선택": "오버" }]), 1);
});

test("ink bounds remove background margins and respect crop bounds/transparency", () => {
  const data = new Uint8ClampedArray(100 * 40 * 4).fill(255);
  for (let y = 5; y < 16; y++) for (let x = 45; x < 55; x++) data.set([0, 130, 210, 255], (y * 100 + x) * 4);
  data.set([0, 0, 0, 0], 0);
  const image = { data, width: 100, height: 40 };
  assert.deepEqual(receiptInkRect(image, {left:0,top:0,width:100,height:20}), {left:45,top:5,width:10,height:11});
  assert.equal(receiptInkRect(image, {left:0,top:20,width:100,height:20}), null);
});

test("double-resolution raster normalizes to the original pixels", () => {
  const original = { width: 4, height: 4, data: Uint8ClampedArray.from({length:64}, (_, i) => i) };
  const doubled = { width: 8, height: 8, data: new Uint8ClampedArray(256) };
  for (let y=0;y<8;y++) for(let x=0;x<8;x++) {
    const p=(Math.floor(y/2)*4+Math.floor(x/2))*4;
    doubled.data.set(original.data.subarray(p,p+4),(y*8+x)*4);
  }
  assert.deepEqual(normalizeReceiptRaster(doubled, [{height:80}]), original);
  assert.equal(normalizeReceiptRaster(original, [{height:40}]), original);
});
