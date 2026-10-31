import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("an absent volleyball fixture remains visibly unlinked without a pick after kickoff", async () => {
  const oldNow = Date.now;
  Date.now = () => Date.parse("2026-10-31T08:00:00Z");
  const server = await createServer({ configFile:false, root:fileURLToPath(new URL("../../",import.meta.url)),
    esbuild:{jsx:"automatic"}, optimizeDeps:{noDiscovery:true,include:[]},
    server:{middlewareMode:true,watch:null,hmr:false},appType:"custom" });
  try {
    const {GameList}=await server.ssrLoadModule("/src/pages/Markets.jsx");
    const row={sport:"vl",league:"KOVO여",home:"GS칼텍스",away:"도로공사",date:"10.31(토) 16:00",market:"승패",n_way:2,odds:[1.72,1.76],result:"홈승"};
    const html=renderToStaticMarkup(createElement(GameList,{data:{live:[],past:[]},marketOdds:{generated_at:"2026-10-31T05:04:00Z",markets:{129:{1527:row}}}}));
    assert.match(html,/배당에서 확인된 미연결 배구 경기 1경기/);
    assert.match(html,/GS칼텍스 vs 도로공사/);
    assert.match(html,/저장된 사전 픽은 확인할 수 없습니다/);
    assert.doesNotMatch(html,/조건에 맞는 경기가 없습니다|1\.72|1\.76|홈승/);
  } finally { await server.close(); Date.now=oldNow; }
});
