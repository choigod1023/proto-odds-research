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
    const { Nav } = await server.ssrLoadModule("/src/components/ui.jsx");
    const nav = renderToStaticMarkup(createElement(Nav, { current: "markets.html" }));
    assert.equal((nav.match(/aria-label="화면 테마"/g) || []).length, 1);
    assert.match(nav, /테마 · 자동/);
    assert.match(nav, /내 기록/);
  } finally { await server.close(); Date.now = originalNow; }
});
