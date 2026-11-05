import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("modal comparison distinguishes estimates, settled history and a frozen final pick", async () => {
  const server = await createServer({server: {middlewareMode: true}, appType: "custom",
    optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {default: View, comparisonRows} = await server.ssrLoadModule("/src/components/MarketPickComparison.jsx");
    const base = {selection_id: "under", offer_id: "a", selection: "언더", odds: 1.9, p_push: 0};
    const scores = [
      ["frontier_m0_actual", .65, .235], ["frontier_m0_projected", .60, .14],
      ["frontier_m1_projected", .58, .102],
    ];
    const comparisons = Object.fromEntries(scores.map(([model, probability, estimated_roi]) =>
      [model, {...base, model, probability_basis: "score_model", probability, estimated_roi}]));
    comparisons.frontier_consensus = {...base, model: "frontier_consensus", probability_basis: "score_model_min",
      probability: .58, estimated_roi: .102};
    comparisons.frontier_tipster = {...base, model: "frontier_tipster", probability_basis: "vote_share",
      probability: 1, estimated_roi: .9};
    const item = {game_no: "3", market: "언더오버", label: "U 8.5", line: 8.5,
      settlement_rule: "total_half", result_period: "full_including_extra", comparisons};
    const recommendations = [{item, comparisonVersion: "exact-market-frontier-v1"}];
    const options = [{selection_id: "under", offer_id: "a", "게임번호": "3", market: item.market,
      label: item.label, line: item.line, "배당": 1.9, "선택": "언더", settlement_rule: item.settlement_rule,
      result_period: item.result_period}];
    const game = {sport: "bs", league: "KBO"};
    const performance = {comparison_by_context: {
      "bs|KBO|언더오버|total_half|8.5|언더|frontier_m1_projected": {hit_rate: .5, hits: 2, decided: 4, bets: 4, roi: -.125},
      "bs|KBO|언더오버|total_half|9.5|언더|frontier_m0_actual": {hit_rate: 1, hits: 7, decided: 7, bets: 7, roi: 1},
    }};
    const html = renderToStaticMarkup(createElement(View, {recommendations, options, game, performance}));
    for (const value of ["세 모델 합의 픽 · 연구용", "M1 선수 효과", "58.0%", "+10.2%", "50.0% (2/4)", "-12.5%", "정산 표본 없음"]) {
      assert.ok(html.includes(value), value);
    }
    assert.doesNotMatch(html, /100\.0%/);
    const changed = [{...options[0], "배당": 1.8}];
    assert.deepEqual(comparisonRows(recommendations, changed), {rows: [], final: null});
    assert.equal(comparisonRows(recommendations.map(r => ({...r, saved: true})), changed).final.arm.odds, 1.9);
    delete comparisons.frontier_m1_projected;
    assert.equal(comparisonRows(recommendations, options).final, null);
    assert.equal(comparisonRows(recommendations.map(r => ({...r, comparisonVersion: undefined})), options).rows.length, 0);

    const model = "kbl-team-margin-handicap-v1";
    const handicap = {...item, market: "핸디캡", line: -3.5, label: "H -3.5",
      settlement_rule: "handicap_half", comparisons: {
        [model]: {...base, selection: "핸디홈", model, probability_basis: "frozen_league_handicap_score_model",
          probability: .60, estimated_roi: .14},
      }};
    const leagueHtml = renderToStaticMarkup(createElement(View, {recommendations: [
      {item: handicap, saved: true, comparisonVersion: "exact-market-frontier-v1"},
    ], game: {sport: "bk", league: "KBL"}}));
    assert.match(leagueHtml, /KBL 팀 점수차 모델/);
    assert.match(leagueHtml, /60\.0%/);
    assert.doesNotMatch(leagueHtml, /세 모델/);

    const international = "international-team-poisson-handicap-v1";
    const market = {...base, selection: "핸디홈", model: "market-baseline-v1",
      probability_basis: "market_inverse_odds", probability: .52, estimated_roi: -.012};
    const research = {...handicap, settlement_rule: "handicap_3way", line: -1, label: "H -1",
      model: market.model, selection_id: base.selection_id, offer_id: base.offer_id, odds: base.odds, comparisons: {
      market,
      [international]: {...base, selection_id: "away", offer_id: "b", selection: "핸디원정",
        model: international, probability_basis: "score_model",
        probability: .60, estimated_roi: .14},
    }};
    const grade = {market: "핸디캡", line: -1, settlement_rule: "handicap_3way", odds: "1.9"};
    const result = {comparisons: {
      market: {...grade, selection_id: "under", offer_id: "a", model: market.model, result: "miss"},
      [international]: {...grade, selection_id: "away", offer_id: "b", model: international, result: "hit"},
    }};
    const renderResearch = (item, result) => renderToStaticMarkup(createElement(View, {recommendations: [
      {item, result, saved: true, comparisonVersion: "exact-market-frontier-v1"},
    ], game: {sport: "sc", league: "EPL"}}));
    const researchHtml = renderResearch(research, result);
    assert.match(researchHtml, /리그별 팀 득점 모델 · 연구 비교/);
    assert.match(researchHtml, /60\.0%/);
    assert.match(researchHtml, /시장 최저 배당.*운영 픽.*>미적중<\/td>/);
    assert.match(researchHtml, /리그별 팀 득점 모델.*>적중<\/td>/);
    assert.doesNotMatch(researchHtml, /세 모델/);
    const wrongGrade = {...result, comparisons: {...result.comparisons,
      [international]: {...result.comparisons[international], line: -2}}};
    assert.match(renderResearch(research, wrongGrade), /리그별 팀 득점 모델.*>결과 대기<\/td>/);
    const marketOnly = renderResearch({...research, comparisons: {market}}, result);
    assert.doesNotMatch(marketOnly, /세 모델|최종 픽 보류|팀 득점 모델/);
  } finally {
    await server.close();
  }
});
