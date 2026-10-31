import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { recordedMarketOffers } from "../lib/recorded-market-offers.js";
test("game details expose a market tab and recorded offers without creating picks",async()=>{
 const server=await createServer({configFile:false,root:fileURLToPath(new URL("../../",import.meta.url)),esbuild:{jsx:"automatic"},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,hmr:false},appType:"custom"});
 try {
  const {Game,RecordedMarketOffers}=await server.ssrLoadModule("/src/pages/Markets.jsx");
  const opts=[{게임번호:"1545",market:"핸디캡",label:"H +1.0",line:1,n_way:3,선택:"핸디홈",배당:1.48},{게임번호:"1545",market:"핸디캡",label:"H +1.0",line:1,n_way:3,선택:"핸디무",배당:4},{게임번호:"1545",market:"핸디캡",label:"H +1.0",line:1,n_way:3,선택:"핸디원정",배당:4.45},{게임번호:"1546",market:"언더오버",label:"U 2.5",line:2.5,n_way:2,선택:"언더",배당:2.18},{게임번호:"1546",market:"언더오버",label:"U 2.5",line:2.5,n_way:2,선택:"오버",배당:1.45}];
  const game={year:2099,round:1,date:"12.31(목) 23:00",sport:"sc",league:"test",home:"A",away:"B",status:"경기전",options:opts};
  const rendered=renderToStaticMarkup(createElement(Game,{g:game,opts,wait:false,detailOnly:true}));assert.match(rendered,/상품·배당/);
  const catalog=renderToStaticMarkup(createElement(RecordedMarketOffers,{offers:recordedMarketOffers(opts)}));
  assert.match(catalog,/1545 · 핸디캡 H \+1\.0/);assert.match(catalog,/1546 · 언더오버 U 2\.5/);assert.match(catalog,/정산 범위 미확인/);assert.match(catalog,/현재 판매 여부/);assert.match(catalog,/원장 일치 여부 미확인/);assert.doesNotMatch(catalog,/선택 목록에 담기|예측 픽|추천/);
  const partial=renderToStaticMarkup(createElement(Game,{g:{...game,options:opts.slice(0,1)},opts:opts.slice(0,1),wait:false,detailOnly:true}));
  assert.match(partial,/상품·배당/);
  for (const waiting of [true,false]) {
    const noOptions=renderToStaticMarkup(createElement(Game,{g:{...game,status:waiting?"배당대기":"경기전",options:[]},opts:[],wait:waiting,detailOnly:true}));
    assert.match(noOptions,/상품·배당/);
    assert.doesNotMatch(noOptions,/배당은 아직 발표되지 않았다|배당 발표 전/);
    const missing=renderToStaticMarkup(createElement(RecordedMarketOffers,{offers:[],waiting}));
    assert.match(missing,/미발매인지 수집 지연인지는 확인되지 않았습니다/);
  }
  const live=recordedMarketOffers(opts.map(row=>({...row,_live:true})),{_liveOddsRecalculatedAt:"2026-10-31T01:00:00Z"});
  const pending=renderToStaticMarkup(createElement(RecordedMarketOffers,{offers:live,hasOptions:true}));
  assert.match(pending,/새로 관측한 배당/);assert.match(pending,/원장 확인 대기/);assert.match(pending,/배당 자료 생성/);
  assert.doesNotMatch(pending,/사전 픽 원장과 일치|기록된 상품과 배당/);
  const empty=renderToStaticMarkup(createElement(RecordedMarketOffers,{offers:recordedMarketOffers(opts.slice(0,1)),hasOptions:true}));
  assert.match(empty,/상품 정보가 불완전하거나 서로 충돌/);assert.doesNotMatch(empty,/1\.48/);
 } finally {await server.close();}
});
