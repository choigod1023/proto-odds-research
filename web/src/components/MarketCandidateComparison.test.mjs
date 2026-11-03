import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("multi-sport comparison keeps line identities and never invents a profitable prediction", async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { default: Comparison, comparisonOffers } = await server.ssrLoadModule("/src/components/MarketCandidateComparison.jsx");
    const make = (no,market,label,line,names) => names.map((name,i) => ({게임번호:no,market,label,line,n_way:names.length,선택:name,배당:1.8+i*.1,시장확률:i===0?.55:.45}));
    const totals = make("2","언더오버","U 2.5",2.5,["언더","오버"]);
    const opts = [...make("1","승패","",null,["홈","원정"]),...totals,
      ...make("3","핸디캡","H -1.5",-1.5,["핸디홈","핸디원정"]),
      ...make("4","전반언더오버","U 1.5",1.5,["전반언더","전반오버"])];
    const candidate={game_no:"2",market:"언더오버",label:"U 2.5",line:2.5,period:"full_game",settlement:"2way",selection:"오버",authors:2,status:"candidate"};
    for (const sport of ["sc","bs","bk","vl"]) {
      const g={sport,market_candidates:{version:"tipster-market-candidates-v1",candidate_only:true,affects_selection:false,affects_probability:false,offers:[candidate]}};
      const offers=comparisonOffers(g,opts);
      assert.equal(offers.length,3);assert.equal(offers[1].candidate.selection,"오버");
      assert.equal(offers[1].selections[0].actionable,false);
      assert.equal(offers[1].selections[0].original,totals[0]);
      const rendered=renderToStaticMarkup(createElement(Comparison,{game:g,options:opts}));
      assert.match(rendered,/언더오버 비교/);assert.match(rendered,/핸디캡 H -1\.5/);
      assert.match(rendered,/픽스터 시장 후보: 오버/);assert.match(rendered,/수익성 미검증/);
      assert.doesNotMatch(rendered,/전반언더|최적|기대수익률|검증된 추천/);
      const altered={...g,market_candidates:{...g.market_candidates,offers:[{...candidate,line:3.5}]}};
      assert.equal(comparisonOffers(altered,opts)[1].candidate,null);
      assert.equal(comparisonOffers({...g,_liveOddsChanged:true},opts)[1].candidate,null);
      assert.equal(comparisonOffers({...g,market_candidates:{...g.market_candidates,affects_selection:true}},opts)[1].candidate,null);
    }
    assert.deepEqual(comparisonOffers({},totals.slice(0,1)),[]);
    const quoted=totals.map((o,i)=>({...o,selection_id:`sel${i}`,offer_id:`off${i}`,period:"full",settlement_scope:"regulation"}));
    const recorded={prediction_status:"recorded_pregame",prediction_revision_id:"revision",prediction_record:{prediction_snapshot_id:"revision",selection_id:"sel0",offer_id:"off0",market:"언더오버",label:"U 2.5",selection:"언더",odds:1.8,captured_at:"2026-11-03T06:00:00Z"}};
    assert.equal(comparisonOffers(recorded,quoted)[0].selections[0].actionable,false);
    const verified=quoted.map(o=>({...o,price_receipt_verified:true}));
    assert.equal(comparisonOffers(recorded,verified)[0].selections[0].actionable,true);
    assert.equal(comparisonOffers(recorded,verified)[0].selections[1].actionable,false);
    assert.equal(comparisonOffers(recorded,verified.map(o=>({...o,settlement_scope:null})))[0].selections[0].actionable,false);
    const empty=renderToStaticMarkup(createElement(Comparison,{game:{},options:[]}));
    assert.match(empty,/비교할 완전한 상품 자료가 없습니다/);
    const unavailable=renderToStaticMarkup(createElement(Comparison,{game:{market_candidates:{status:"tipster_input_unavailable"}},options:totals}));
    assert.match(unavailable,/픽스터 자료 조회 실패/);assert.doesNotMatch(unavailable,/경기 전 픽스터 의견 없음/);
  } finally { await server.close(); }
});
