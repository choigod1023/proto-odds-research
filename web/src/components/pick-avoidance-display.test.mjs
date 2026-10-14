import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("withhold explanation is absent from both panels without hiding other information", async () => {
  const server = await createServer({
    configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" },
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null },
    appType: "custom",
  });
  try {
    const { AiDecisionPath } = await server.ssrLoadModule("/src/components/AiDisclosure.jsx");
    const { default: PredictionPanel } = await server.ssrLoadModule("/src/components/PredictionPanel.jsx");
    const decision = {
      action: "withhold", probability: { market: .6, final: .6, aiDeltaApplied: 0 },
      stages: [], evidence: [],
      withholdReasons: [{
        title: "현재 안전조건을 만족하는 선택이 없음",
        body: "배당은 있지만 마켓 유효성·가격 범위·동일 마켓 최유력 조건을 함께 통과한 선택이 없습니다. 어느 조건을 어겼는지 확정되기 전에는 가지 않습니다.",
      }],
    };
    const before = structuredClone(decision);
    const path = renderToStaticMarkup(createElement(AiDecisionPath, { decision }));
    const prediction = renderToStaticMarkup(createElement(PredictionPanel, {
      analysis: { decision, reasons: ["최근 전적 — 3승 2패"], cautions: ["선발 변경 가능성"] },
    }));
    for (const html of [path, prediction]) {
      assert.doesNotMatch(html, /이 픽을 가지 말아야 하는 이유/);
      assert.doesNotMatch(html, /현재 안전조건을 만족하는 선택이 없음|어느 조건을 어겼는지/);
    }
    assert.match(path, /최종 확률 계산 경로/);
    assert.match(prediction, /최근 전적|3승 2패/);
    assert.match(prediction, /반대 근거·변수/);
    assert.match(prediction, /선발 변경 가능성/);
    assert.doesNotMatch(prediction, /아래 위험/);
    assert.deepEqual(decision, before);

    const closed = renderToStaticMarkup(createElement(AiDecisionPath, {
      decision: { ...decision, action: "closed" },
    }));
    assert.match(closed, /경기 시작을 확인/);
    const stale = renderToStaticMarkup(createElement(AiDecisionPath, {
      decision: { ...decision, action: "recalculating" },
    }));
    assert.match(stale, /실시간 배당이 바뀌어/);
  } finally {
    await server.close();
  }
});
