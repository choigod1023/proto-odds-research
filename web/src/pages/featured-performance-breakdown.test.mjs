import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("featured performance exposes sport and market cohorts without replacing the saved total", async () => {
  const server = await createServer({ optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false }, appType: "custom" });
  try {
    const { FeaturedPickPerformance } = await server.ssrLoadModule("/src/components/PregameRecommendation.jsx");
    const overall = { captured: 114, settled: 75, decided: 75, hits: 36, refunds: 0, pending: 39, hit_rate: .48, roi: -.22 };
    const basketball = { captured: 34, decided: 23, hits: 11, pending: 11, hit_rate: 11 / 23, roi: -.19 };
    const html = renderToStaticMarkup(createElement(FeaturedPickPerformance, { data: {
      ...overall, by_sport: { bk: basketball }, by_market: { "언더오버": basketball },
      selection_rule: { preferred_odds: 1.5 },
    } }));
    assert.match(html, /36\/75/);
    assert.match(html, /종목별 1픽 성적/);
    assert.match(html, /농구/);
    assert.match(html, /11\/23/);
    assert.match(html, /47\.83%/);
    assert.match(html, /시장별 1픽 성적/);
    assert.match(html, /배당 1\.50 이상/);
    assert.match(html, /일반 승패 추천과 성적이 다를/);
    const legacy = renderToStaticMarkup(createElement(FeaturedPickPerformance, { data: overall }));
    assert.match(legacy, /36\/75/);
    assert.doesNotMatch(legacy, /종목별 1픽 성적|NaN/);
  } finally { await server.close(); }
});
