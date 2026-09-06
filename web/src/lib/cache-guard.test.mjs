import test from "node:test";
import assert from "node:assert/strict";
import { assetSignatureFromHtml, installCacheGuard } from "./cache-guard.js";

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

async function runGuard(t, latestHtml) {
  const initialHtml = '<script src="./assets/research-old.js"></script>';
  const replacements = [];
  const listeners = {};
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, text: async () => latestHtml }));
  for (const [key, value] of Object.entries({
    window: {
      location: { href: "https://example.com/research.html#ai-model", replace: (url) => replacements.push(url) },
      addEventListener: (event, handler) => { listeners[event] = handler; },
    },
    document: { documentElement: { outerHTML: initialHtml }, addEventListener() {} },
  })) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    t.after(() => descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]);
  }
  installCacheGuard({ minimumIntervalMs: 0 });
  // Vite inserts preload links while the fresh HTML request is still pending.
  document.documentElement.outerHTML += '<link rel="modulepreload" href="./assets/Research-lazy.js"><link rel="stylesheet" href="./assets/Research-lazy.css">';
  await new Promise((resolve) => setImmediate(resolve));
  return { replacements, listeners };
}

test("lazy 페이지의 런타임 자산 추가는 최초 확인과 재포커스에서 새로고침하지 않는다", async (t) => {
  const { replacements, listeners } = await runGuard(t, '<script src="./assets/research-old.js"></script>');
  await listeners.focus();
  await listeners.pageshow();
  assert.deepEqual(replacements, []);
});

test("실제 배포 자산 변경은 새로고침하고 페이지 앵커를 유지한다", async (t) => {
  const { replacements } = await runGuard(t, '<script src="./assets/research-new.js"></script>');
  assert.equal(replacements.length, 1);
  const target = new URL(replacements[0]);
  assert.equal(target.pathname, "/research.html");
  assert.equal(target.hash, "#ai-model");
  assert.ok(target.searchParams.has("_fresh"));
});
