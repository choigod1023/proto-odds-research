import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("today list, phase count and top board all retain the exact overnight pick", async () => {
  const now = Date.parse("2026-09-06T00:25:00+09:00");
  const realNow = Date.now;
  Date.now = () => now;
  const server = await createServer({ configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, hmr: false }, appType: "custom" });
  try {
    const { GameList } = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const game = { year: 2026, round: 105, date: "09.05(토) 23:00", sport: "sc", league: "EPL",
      home: "노팅엄F", away: "토트넘", status: "경기전", options: [],
      _liveState: { status: "STARTED", status_text: "후반 20분", home_score: 1, away_score: 0,
        clock: { elapsed_minute: 65 }, observed_at: new Date(now).toISOString() },
      prediction_record: { selection_id: "prior", market: "승무패", selection: "홈", odds: 1.6,
        probability: .6, captured_at: "2026-09-05T21:00:00+09:00" } };
    const render = row => renderToStaticMarkup(createElement(GameList, {
      data: { year: 2026, live: [row], past: [] }, grades: { odds_bins: [] },
      liveChecked: true, liveGeneratedAt: new Date(now).toISOString() }));
    const html = render(game);
    assert.match(html, /전날 시작 · 계속 추적/);
    assert.match(html, /전날 시작 · 저장된 사전 픽 계속 추적/);
    assert.match(html, /match-card is-live/);
    assert.match(html, /aria-pressed="false">진행 중 1<\/button>/);
    assert.match(html, /1.60배/);
    const stale = render({ ...game, _liveState: { ...game._liveState, observed_at: "2026-09-05T23:50:00+09:00" } });
    assert.match(stale, /match-card is-pending/);
    assert.match(stale, /노팅엄F/);
    const ended = render({ ...game, _liveState: { ...game._liveState, finished: true } });
    assert.doesNotMatch(ended, /match-card is-live|전날 시작 · 계속 추적/);
  } finally {
    await server.close();
    Date.now = realNow;
  }
});
