import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("decided picks replace probability in list, detail, top board and personal records while match stays LIVE", async () => {
  const now = Date.parse("2026-09-06T11:00:00Z"), originalNow = Date.now;
  Date.now = () => now;
  const server = await createServer({configFile:false,root:fileURLToPath(new URL("../../",import.meta.url)),
    esbuild:{jsx:"automatic"},optimizeDeps:{noDiscovery:true,include:[]},
    server:{middlewareMode:true,watch:null,hmr:false},appType:"custom"});
  try {
    const {Game} = await server.ssrLoadModule("/src/pages/Markets.jsx");
    const {TodayPicksBoard} = await server.ssrLoadModule("/src/components/TodayPicksBoard.jsx");
    const {BetCard} = await server.ssrLoadModule("/src/pages/Dashboard.jsx");
    const {default:RecordMatchDetails} = await server.ssrLoadModule("/src/components/RecordMatchDetails.jsx");
    const lv = {status:"STARTED",home_score:5,away_score:4,status_text:"7회초",observed_at:new Date(now).toISOString()};
    const g = {year:2026,round:106,date:"09.06(일) 18:00",sport:"bs",league:"NPB",home:"홈팀",away:"원정팀",options:[],
      status:"경기전",_liveStarted:true,_liveState:lv,prediction_record:{selection_id:"saved",market:"언더오버",label:"U 8.5",
        selection:"오버",odds:1.8,probability:.6,captured_at:"2026-09-06T08:00:00Z",is_market_favorite:true}};
    const render = (component,props) => renderToStaticMarkup(createElement(component,props));
    for (const [choice,label] of [["오버","적중"],["언더","적중실패"]]) {
      const selected={...g,prediction_record:{...g.prediction_record,selection:choice}};
      for (const detailOnly of [false,true]) {
        const html=render(Game,{g:selected,lv,opts:[],wait:true,stale:false,grades:{odds_bins:[]},year:2026,detailOnly});
        assert.match(html,new RegExp(label));
        assert.match(html,/LIVE/);
        assert.match(html,/공식 정산 전/);
        assert.doesNotMatch(html,/99\.0%|계산 불가|현재 적중 확률\(추정\)/);
      }
    }
    const board=render(TodayPicksBoard,{games:[g],now});
    assert.match(board,/픽 판정/); assert.match(board,/적중/); assert.match(board,/1\.80배/);
    assert.doesNotMatch(board,/종료된 사전 픽/);
    const half={...g,sport:"sc",prediction_record:{...g.prediction_record,market:"전반승무패",label:"h(전반)",selection:"전반무",probability:null}};
    const halfLive={...lv,current_period:2,first_half_complete:true,first_half_score:[0,0],home_score:0,away_score:2,status_text:"후반 20분"};
    const halfHtml=render(Game,{g:half,lv:halfLive,opts:[],wait:true,grades:{odds_bins:[]},year:2026,detailOnly:true});
    assert.match(halfHtml,/적중/); assert.match(halfHtml,/전반 종료 점수/); assert.match(halfHtml,/기록 없음/);
    assert.doesNotMatch(halfHtml,/계산 불가/);
    const bet={id:"bet",game:g,selection:{market:"언더오버",label:"U 8.5",choice:"오버"},purchaseOdds:1.8,stake:1000,
      openingProbability:null,createdAt:g.prediction_record.captured_at};
    for (const component of [BetCard,RecordMatchDetails]) {
      const html=render(component,{bet,live:lv});
      assert.match(html,/적중/); assert.match(html,/공식 정산 전/);
      assert.doesNotMatch(html,/99\.0%|계산 불가|확정 손익/);
    }
  } finally {await server.close();Date.now=originalNow;}
});
