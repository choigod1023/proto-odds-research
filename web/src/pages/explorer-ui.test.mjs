import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("explorer groups by sport and league and renders only the first league without pagination", async () => {
  const originalNow = Date.now;
  Date.now = () => Date.parse("2099-09-06T00:00:00Z");
  const server = await createServer({ configFile: false,
    root: fileURLToPath(new URL("../../", import.meta.url)),
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, hmr: false }, appType: "custom" });
  try {
    const { GameList, Game } = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const base = { year: 2099, date: "09.06(일) 18:00", round: 1, status: "경기전", options: [], sport: "bs" };
    const games = Array.from({ length: 40 }, (_, index) => ({ ...base,
      league: index < 2 ? "A리그" : "B리그", home: `홈${index}`, away: `원정${index}` }));
    const html = renderToStaticMarkup(createElement(GameList, { data: { live: games, past: [], year: 2099 } }));
    assert.match(html, /2개 리그 · 40경기/);
    assert.equal((html.match(/class="league-heading"/g) || []).length, 2);
    assert.equal((html.match(/class="match-row game-info-trigger"/g) || []).length, 2);
    assert.match(html, /aria-expanded="true"/);
    assert.match(html, /aria-expanded="false"/);
    assert.doesNotMatch(html, /경기 목록 페이지|pagination/);
    assert.ok(html.indexOf("종목 필터") < html.indexOf("리그별 경기"));
    assert.match(html, /추천만 보기/);
    const row = renderToStaticMarkup(createElement(Game, { g: { ...games[0], form_home: { last10: "7승 3패" } }, opts: [], wait: false }));
    assert.match(row, /경기력/);
    assert.match(row, /7승 3패/);
    assert.match(row, /상세 근거/);
    const recommendationProps = { g: { ...games[0], form_home: { last10: "7승 3패" }, form_away: { last10: "4승 6패" } },
      opts: [], wait: false, highlightedToday: true,
      todayOption: { market: "승패", 선택: "홈", 배당: 1.8, 시장확률: .6 },
      todayMembership: { recommended: true, reason: "55% 기준을 통과했다", display: { text: "설정 55%" }, counterReason: "정책 통과" } };
    for (const detailOnly of [false, true]) {
      const rendered = renderToStaticMarkup(createElement(Game, { ...recommendationProps, detailOnly }));
      assert.match(rendered, /7승 3패/);
      assert.match(rendered, /4승 6패/);
      assert.doesNotMatch(rendered, /55%|정책 통과|사전 픽은 고정/);
    }
    const { Nav } = await server.ssrLoadModule("/src/components/ui.jsx");
    const nav = renderToStaticMarkup(createElement(Nav, { current: "markets.html" }));
    assert.equal((nav.match(/aria-label="화면 테마"/g) || []).length, 1);
    assert.match(nav, /테마 · 자동/);
    assert.match(nav, /내 기록/);
  } finally { await server.close(); Date.now = originalNow; }
});
