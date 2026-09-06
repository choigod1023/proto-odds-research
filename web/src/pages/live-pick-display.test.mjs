import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("live card renders the saved pick and prior even with no current options", async () => {
  const now = Date.parse("2026-09-05T11:00:00Z");
  const originalNow = Date.now;
  Date.now = () => now;
  const server = await createServer({ configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null }, appType: "custom" });
  try {
    const { Game, GameList } = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const lv = { status: "STARTED", status_text: "6회초", home_score: 4, away_score: 1 };
    const g = { year: 2026, round: 105, date: "09.05(토) 18:00", sport: "bs",
      home: "홈팀", away: "원정팀", status: "경기전", options: [],
      _liveStarted: true, _liveFeedAt: new Date(now).toISOString(),
      prediction_record: { selection_id: "saved", market: "승패", selection: "홈", label: "",
        odds: 1.8, probability: .57, captured_at: "2026-09-05T08:00:00Z" } };
    const props = { g, opts: [], lv, wait: true, stale: true, grades: { odds_bins: [] }, year: 2026, detailOnly: true };
    const row = renderToStaticMarkup(createElement(Game, { ...props, detailOnly: false }));
    assert.match(row, /aria-haspopup="dialog"/);
    assert.doesNotMatch(row, /match-detail|<summary/);
    const html = renderToStaticMarkup(createElement(Game, props));
    assert.match(html, /경기 전 예측 픽/);
    assert.match(html, /57.0%/);
    assert.match(html, /현재 적중 확률/);
    assert.match(html, /경기 전 적중 확률/);
    assert.doesNotMatch(html, /NaN/);
    const missingProbability = renderToStaticMarkup(createElement(Game, { ...props,
      g: { ...g, prediction_record: { ...g.prediction_record, probability: null } } }));
    assert.match(missingProbability, /경기 전 예측 픽/);
    assert.match(missingProbability, /경기 전 적중 확률 기록 없음/);
    assert.match(missingProbability, /계산 불가/);
    assert.doesNotMatch(missingProbability, /0.0%/);
    const unrecorded = renderToStaticMarkup(createElement(Game, { ...props,
      g: { ...g, prediction_record: null },
      todayOption: { market: "승패", 선택: "원정", 시장확률: .9 } }));
    assert.match(unrecorded, /사전 예측 기록 없음/);
    assert.doesNotMatch(unrecorded, /경기 전 예측 픽|90.0%/);
    assert.match(unrecorded, /경기 전 적중 확률/);
    assert.match(unrecorded, /현재 적중 확률/);
    assert.match(unrecorded, /계산 불가/);
    const stale = renderToStaticMarkup(createElement(Game, { ...props,
      g: { ...g, _liveFeedAt: "2026-09-05T10:00:00Z" } }));
    assert.match(stale, /57.0%/);
    assert.match(stale, /현재 적중 확률/);
    assert.match(stale, /확인 중/);
    assert.match(stale, /실시간 점수 업데이트가 늦어/);
    const unsupported = renderToStaticMarkup(createElement(Game, { ...props, detailOnly: false,
      g: { ...g, prediction_record: { ...g.prediction_record, market: "핸디캡", label: "H -1.5", selection: "핸디홈" } } }));
    assert.match(unsupported, /경기 전 적중 확률/);
    assert.match(unsupported, /57.0%/);
    assert.match(unsupported, /현재 적중 확률/);
    assert.match(unsupported, /계산 불가/);
    assert.match(unsupported, /아직 지원하지 않습니다/);
    const list = renderToStaticMarkup(createElement(GameList, {
      data: { live: [{ ...g, league: "NPB", _liveState: lv }], past: [], year: 2026 },
      grades: { odds_bins: [] }, today: null,
      liveChecked: true, liveGeneratedAt: "2026-09-05T10:00:00Z",
    }));
    assert.match(list, /홈팀/);
    assert.match(list, /경기정보 열기/);
    assert.doesNotMatch(list, /match-detail/);
    assert.match(list, /실시간 점수 갱신 지연/);
    assert.match(list, /recommendation-overview/);
    assert.ok(list.indexOf("종목 필터") < list.indexOf("경기 목록"));
    for (const [home_score, away_score, result] of [[4, 1, "적중"], [1, 4, "적중실패"]]) {
      const finished = renderToStaticMarkup(createElement(Game, { ...props,
        detailOnly: false, g: { ...g, options: [{ selection_id: "saved", market: "승패", 선택: "홈", 배당: 9.9 }] },
        lv: { status: "RESULT", finished: true, home_score, away_score } }));
      const summary = finished.split("</button>")[0];
      assert.match(summary, new RegExp(result));
      assert.match(summary, /당시 배당 1.80배/);
      assert.doesNotMatch(summary, /9.90/);
    }
  } finally {
    await server.close();
    Date.now = originalNow;
  }
});
