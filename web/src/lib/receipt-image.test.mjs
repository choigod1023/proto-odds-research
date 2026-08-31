import test from "node:test";
import assert from "node:assert/strict";
import { buttonChoiceIndex } from "./receipt-image.js";

const threeWay = [{ "선택": "핸디홈" }, { "선택": "핸디무" }, { "선택": "핸디원정" }];

test("선택 버튼 OCR 글자로 데스크톱 3지선다 픽을 판별한다", () => {
  assert.equal(buttonChoiceIndex("승 1.51", threeWay), 0);
  assert.equal(buttonChoiceIndex("패 1.79", threeWay), 2);
});

test("언더오버 버튼도 위치가 아니라 글자로 판별한다", () => {
  assert.equal(buttonChoiceIndex("오버 1.75", [{ "선택": "언더" }, { "선택": "오버" }]), 1);
});
