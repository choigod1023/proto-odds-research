import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

test('recommendations preserve product identity and never turn votes into probabilities', async () => {
 const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
 try {
  const {default:View,currentMarketRecommendations}=await server.ssrLoadModule('/src/components/MarketRecommendations.jsx');
  const options=['언더','오버'].map((name,i)=>({게임번호:'2',market:'언더오버',label:'U 2.5',line:2.5,n_way:2,선택:name,배당:i ? 1.9 : 1.8,selection_id:`s${i}`,offer_id:`o${i}`,period:'full_game',settlement_scope:'regulation'}));
  const item={selection_id:'s1',offer_id:'o1',market:'언더오버',label:'U 2.5',line:2.5,selection:'오버',odds:1.9,probability:.57,probability_basis:'m0_score_model_conditional_decisive',model:'M0',estimated_roi:.083,p_push:.1,validation_status:'research_unvalidated',source_ids:['source'],settlement_rule:'regulation'};
  const game={options,market_recommendations:{version:'multi-market-recommendations-v1',items:[item,item]}};
  const found=currentMarketRecommendations(game,options);
  assert.equal(found.length,1);assert.equal(found[0].option,options[1]);
  const html=renderToStaticMarkup(createElement(View,{recommendations:found,onSelect:()=>{}}));
  for(const text of ['사전 픽','오버','수익성 미검증','57.0%','8.3%','10.0%','M0','source','선택 목록']) assert.ok(html.includes(text),text);
  const vote=renderToStaticMarkup(createElement(View,{recommendations:[{item:{...item,probability_basis:'vote_share'},option:options[1]}]}));
  assert.doesNotMatch(vote,/57\.0%|8\.3%/);assert.match(vote,/예상 적중확률 미제공/);
  const market=renderToStaticMarkup(createElement(View,{recommendations:[{item:{...item,probability_basis:'market_conditional_decisive'},option:options[1]}]}));
  assert.match(market,/57\.0%/);assert.match(market,/배당 기반 · 무효 제외 조건부/);
  for(const change of [{offer_id:'bad'},{line:3.5},{odds:2},{selection:'언더'},{label:'U 3.5'}]) assert.equal(currentMarketRecommendations({...game,market_recommendations:{...game.market_recommendations,items:[{...item,...change}]}},options).length,0);
  assert.equal(currentMarketRecommendations({...game,_liveOddsChanged:true},options).length,0);
  assert.equal(currentMarketRecommendations(game,[...options,options[1]]).length,0);
  assert.equal(currentMarketRecommendations(game,options.map(o=>({...o,settlement_scope:null}))).length,0);
  assert.equal(currentMarketRecommendations({...game,market_recommendations:{version:'unknown',items:[item]}},options).length,0);
 } finally {await server.close();}
});


test('market performance distinguishes pending capture from zero-return settlement', async () => {
 const server=await createServer({server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
 try {
  const {MarketRecommendationPerformance:View}=await server.ssrLoadModule('/src/components/MarketRecommendations.jsx');
  const html=renderToStaticMarkup(createElement(View,{data:{by_market:{언더오버:{captured:4,bets:0},핸디캡:{captured:2,bets:2,hits:1,decided:2,refunds:0,roi:0}}}}));
  assert.match(html,/기록 4건 · 정산 대기 · ROI 미산출/);
  assert.match(html,/정산 2건 · 적중 1\/2 · 환불 0건 · ROI 0.00%/);
  assert.match(html,/지갑과 별도/);
 } finally {await server.close();}
});
