import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("board SSR shows original NPB picks, evolving estimate and final result above original odds", async () => {
  const now = Date.parse("2026-09-05T07:00:00Z");
  const server = await createServer({ configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null }, appType: "custom" });
  try {
    const { TodayPicksBoard } = await server.ssrLoadModule("/src/components/TodayPicksBoard.jsx");
    const base = { year: 2026, date: "09.05(토) 14:00", round: 105,
      league: "NPB", sport: "bs", home: "오릭스", away: "상대팀", options: [],
      _liveState: { status: "STARTED", status_text: "6회초", home_score: 4, away_score: 1 },
      _liveFeedAt: new Date(now).toISOString(),
      prediction_record: { selection_id: "old", market: "승패", label: "", selection: "홈",
        probability: .5607, odds: 1.59, captured_at: "2026-09-05T03:00:00Z" } };
    const render = (games, today = { candidates: [] }) => renderToStaticMarkup(
      createElement(TodayPicksBoard, { games, today, now }));
    const html = render([base, { ...base, home: "소프트뱅크", prediction_record: {
      ...base.prediction_record, probability: .5897, odds: 1.52 } }]);
    assert.match(html, /오늘의 추천 픽/);
    assert.match(html, /오릭스/);
    assert.match(html, /소프트뱅크/);
    assert.match(html, /56.1%/);
    assert.match(html, /59.0%/);
    assert.match(html, /1.59배/);
    assert.match(html, /1.52배/);
    assert.match(html, /사전 픽 · 추천 이력 미확인/);
    assert.match(html, /현재 추정/);
    assert.match(html, /grid-cols-1.*sm:grid-cols-2.*xl:grid-cols-3/);
    assert.doesNotMatch(html, /NaN|55% 기준|60% 기준/);
    const ended = render([{ ...base, _liveState: { ...base._liveState, finished: true } }]);
    assert.match(ended, /적중/);
    assert.match(ended, /56.1%/);
    assert.ok(ended.indexOf("적중") < ended.lastIndexOf("당시 배당"));
    assert.match(ended, /1.59배/);
    const stale = render([{ ...base, _liveFeedAt: "2026-09-05T05:00:00Z" }]);
    assert.match(stale, /중계 갱신이 늦어/);
    assert.match(stale, /56.1%/);
    const unsupported = render([{ ...base, prediction_record: { ...base.prediction_record,
      market: "핸디캡", label: "H -1.5", selection: "핸디홈" } }]);
    assert.match(unsupported, /지원하지 않습니다/);
    assert.match(unsupported, /핸디홈/);
    const missing = render([{ ...base, prediction_record: null }]);
    assert.match(missing, /오늘 확인된 추천·사전 픽이 없습니다/);
    assert.doesNotMatch(missing, /56.1%|1.59배/);
    const dupe = render([base, { ...base, round: 106 }]);
    assert.equal((dupe.match(/<article/g) || []).length, 1);
  } finally {
    await server.close();
  }
});
