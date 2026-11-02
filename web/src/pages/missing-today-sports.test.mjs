import assert from "node:assert/strict";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createServer} from "vite";

test("fresh odds cannot hide an old prediction list or its missing baseball and basketball fixtures",async()=>{
 const oldNow=Date.now;Date.now=()=>Date.parse("2026-11-01T03:50:00Z");
 const server=await createServer({configFile:false,root:fileURLToPath(new URL("../../",import.meta.url)),
   esbuild:{jsx:"automatic"},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,hmr:false},appType:"custom"});
 try {
  const {GameList}=await server.ssrLoadModule("/src/pages/Markets.jsx");
  const base={market:"승패",n_way:2,date:"11.01(일) 14:00",odds:[1.73,1.75]};
  const data={generated_at:"2026-10-30T10:13:55Z",live:[],past:[]};
  const odds={generated_at:"2026-11-01T03:33:51Z",markets:{129:{
    a:{...base,sport:"bs",league:"KBO",home:"삼성",away:"KIA"},
    b:{...base,sport:"bk",league:"KBL",home:"창원LG",away:"원주DB"},
    c:{...base,sport:"vl",league:"KOVO남",home:"KB손보",away:"삼성화재"}}}};
  const html=renderToStaticMarkup(createElement(GameList,{data,marketOdds:odds,stale:false}));
  assert.match(html,/삼성 vs KIA/);assert.match(html,/창원LG vs 원주DB/);assert.match(html,/KB손보 vs 삼성화재/);
  assert.match(html,/배당에서 확인된 미연결 경기 3건/);assert.match(html,/원천 회차 미연결 3건 · 다른 회차 연결 0건 · 상태 확인 필요 3건/);
  assert.match(html,/경기별 판정 목록 갱신 지연/);assert.match(html,/마지막 판정 생성/);
  assert.doesNotMatch(html,/조건에 맞는 경기가 없습니다|예정 3|1\.73|1\.75|승패 홈|미연결 배구/);
 }finally{await server.close();Date.now=oldNow;}
});
