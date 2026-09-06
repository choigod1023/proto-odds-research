import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

test("receipt UI offers independent album, camera, scoped paste and local-only OCR", async () => {
  const server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" },
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, hmr: false },
    appType: "custom",
  });
  try {
    const { default: Ocr, ReceiptLineInput } = await server.ssrLoadModule(
      "/src/components/ReceiptOcr.jsx",
    );
    const html = renderToStaticMarkup(createElement(Ocr));
    assert.match(html, /앨범·파일/);
    assert.match(html, /클립보드 이미지 붙여넣기/);
    assert.match(html, /aria-label="이미지 붙여넣기 및 끌어놓기 영역"/);
    assert.match(html, /class="receipt-image-input-album"[^>]*type="file"/);
    assert.doesNotMatch(
      html.match(/<input[^>]*class="receipt-image-input-album"[^>]*>/)?.[0] ||
        "",
      /capture=/,
    );
    assert.match(
      html,
      /class="receipt-image-input-camera"[^>]*capture="environment"/,
    );
    for (const value of ["+1.0", "-1.0", "0", "182.5", ""]) {
      const line = renderToStaticMarkup(
        createElement(ReceiptLineInput, {
          label: "3번 기준점",
          value,
          onChange: () => {},
        }),
      );
      assert.match(line, /type="text"/);
      assert.match(line, /inputMode="text"/);
      assert.ok(line.includes(`value="${value}"`));
    }
  } finally {
    await server.close();
  }
});
