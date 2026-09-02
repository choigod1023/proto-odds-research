import test from "node:test";
import assert from "node:assert/strict";
import { assetSignatureFromHtml } from "./cache-guard.js";

test("해시 자산 순서가 달라도 같은 배포로 판정한다", () => {
  const a = '<script src="./assets/app-123.js"></script><link href="./assets/ui-456.css">';
  const b = '<link href="/repo/assets/ui-456.css"><script src="/repo/assets/app-123.js"></script>';
  assert.equal(assetSignatureFromHtml(a), assetSignatureFromHtml(b));
});

test("새 빌드의 해시 파일은 다른 배포로 판정한다", () => {
  assert.notEqual(
    assetSignatureFromHtml('<script src="./assets/app-old.js"></script>'),
    assetSignatureFromHtml('<script src="./assets/app-new.js"></script>'),
  );
});
