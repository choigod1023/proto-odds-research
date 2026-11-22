import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('the pregame pick area includes winner, handicap and total in the card and detail',async()=>{
 const now=Date.now;Date.now=()=>Date.parse('2099-09-06T00:00:00Z');
 const server=await createServer({configFile:false,root:fileURLToPath(new URL('../../',import.meta.url)),esbuild:{jsx:'automatic'},optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,watch:null,hmr:false},appType:'custom'});
 try {
  const {Game}=await server.ssrLoadModule('/src/pages/Markets.jsx');
  const options=[
    ...['언더','오버'].map((name,i)=>({게임번호:'2',market:'언더오버',label:'U 2.5',line:2.5,n_way:2,선택:name,배당:i?1.9:1.8,selection_id:`s${i}`,offer_id:`o${i}`,period:'full_game',settlement_scope:'regulation'})),
    ...['핸디홈','핸디원정'].map((name,i)=>({게임번호:'3',market:'핸디캡',label:'H -1.5',line:-1.5,n_way:2,선택:name,배당:i?1.9:1.8,selection_id:`h${i}`,offer_id:`ho${i}`,period:'full_game',settlement_scope:'regulation'})),
  ];
  const winnerOptions=['홈','원정'].map((name,i)=>({게임번호:'1',market:'승패',n_way:2,선택:name,배당:i?1.7:2.1,시장확률:i?.55:.45,selection_id:`w${i}`,offer_id:`wo${i}`}));
  const item={selection_id:'s1',offer_id:'o1',market:'언더오버',label:'U 2.5',line:2.5,selection:'오버',odds:1.9,game_no:'2',settlement_rule:'regulation',model:'market-baseline-v1',automatic_recommendation:true};
  const handicap={...item,selection_id:'h0',offer_id:'ho0',market:'핸디캡',label:'H -1.5',line:-1.5,selection:'핸디홈',odds:1.8,game_no:'3'};
  const g={year:2099,date:'09.06(일) 18:00',round:1,status:'경기전',sport:'bs',league:'리그',home:'홈팀',away:'원정팀',options:[...options,...winnerOptions],market_recommendations:{version:'multi-market-recommendations-v1',items:[item,handicap]}};
  const featured = {prediction_snapshot_id:'snapshot',kickoff:'2099-09-06T09:00:00Z',
    captured_at:'2099-09-06T07:00:00Z',
    pick:{...handicap,version:'featured-pregame-v1',probability:.514,estimated_roi:-.075}};
  Object.assign(g,{prediction_revision_id:'snapshot',featured_pick_record:featured});
  const props={g,opts:g.options,wait:false,onSaveBet:()=>{},todayOption:winnerOptions[0]};
  for(const detailOnly of [false,true]){
    const html=renderToStaticMarkup(createElement(Game,{...props,detailOnly}));
    const pickArea = detailOnly
      ? html.split('aria-label="경기 예측"')[1].split('class="match-detail"')[0]
      : html.split('class="match-call-inline"')[1].split('<span class="flex flex-wrap items-start gap-1.5">')[0];
    assert.match(pickArea,/예측 픽/);
    assert.match(pickArea,/승패/);
    assert.match(pickArea,/핸디캡 H -1\.5 · 핸디홈/);
    assert.equal((html.match(/aria-label="경기 추천 1픽"/g) || []).length,1);
    assert.match(pickArea,/경기 추천 1픽.*핸디캡 H -1\.5 · 핸디홈.*배당 기준 적중 가능성 51\.4%/);
    if(detailOnly) {
      assert.match(pickArea,/핸디캡·언더오버 사전 픽 2건/);
      assert.doesNotMatch(html,/모델별 픽 비교|이 추천을 선택 목록에 담기|언더오버 U 2\.5 · 오버/);
      const marketTab=renderToStaticMarkup(createElement(Game,{...props,detailOnly:true,initialDetailTab:'markets'}));
      assert.match(marketTab,/언더오버 U 2\.5 · 오버.*1\.90배/);
      assert.match(marketTab,/이 추천을 선택 목록에 담기/);
      assert.ok(marketTab.indexOf('핸디캡 · 언더오버 사전 픽') < marketTab.indexOf('모델별 픽 비교'));
    } else {
      assert.match(pickArea,/언더오버 U 2\.5 · 오버/);
      assert.match(pickArea,/1\.90배.*시장 배당 기준/);
      assert.doesNotMatch(html,/모델별 픽 비교/);
      assert.match(pickArea,/<b>승패 홈 · <span[^>]*>2\.10배<\/span><\/b>/);
      assert.match(html,/title="경기 추천 1픽 · 핸디캡 H -1\.5 · 핸디홈 배당"[^>]*>.*?핸디홈 · H -1\.5<\/span><span[^>]*>1\.80<\/span>/);
      assert.doesNotMatch(html,/title="승패 · 홈 배당"|배당 기준 확률 45\.0%/);
    }
  }
  const fallback = renderToStaticMarkup(createElement(Game,{...props,g:{...g,options:[],market_recommendations:null}}));
  assert.doesNotMatch(fallback,/경기 추천 1픽/);
  assert.match(fallback,/title="승패 · 홈 배당"[^>]*>.*?홈<\/span><span[^>]*>2\.10<\/span>/);
  const marketOnly=renderToStaticMarkup(createElement(Game,{...props,g:{...g,options},opts:options,todayOption:null,detailOnly:true,initialDetailTab:'markets'}));
  assert.match(marketOnly,/언더오버 U 2\.5 · 오버/);
  const fixture=Object.fromEntries(['sport','league','home','away','year','round','date'].map(k=>[k,g[k]]));
  const record={prediction_snapshot_id:'snapshot',fixture,kickoff:'2099-09-06T09:00:00Z',captured_at:'2099-09-06T07:00:00Z',
    market_recommendations:{version:'multi-market-recommendations-v1',generated_at:'2099-09-06T06:59:00Z',items:[item]},
    market_recommendation_options:options,
    market_recommendation_results:[{result:'hit',selection_id:'s1',offer_id:'o1',market:'언더오버',line:2.5,settlement_rule:'regulation',odds:'1.9',net_units:'0.9'}]};
  Date.now=()=>Date.parse('2099-09-06T08:45:00Z');
  for(const detailOnly of [false,true]){
    const html=renderToStaticMarkup(createElement(Game,{...props,g:{...g,market_prediction_record:record,featured_pick_record:{...featured,result:{result:"hit"}}},detailOnly,initialDetailTab:'markets'}));
    if(detailOnly) {
      assert.match(html,/보존된 경기 전 추천.*순손익 \+0\.90단위/);
    } else {
      assert.match(html,/보존된 시장별 추천.*1\.90배.*적중/);
      assert.doesNotMatch(html,/2099-09-06T07:00:00Z|순손익/);
    }
    assert.doesNotMatch(html,/이 추천을 선택 목록에 담기/);
    assert.match(html,/경기 추천 1픽 · 사전 보존 · 적중.*핸디캡 H -1\.5 · 핸디홈/);
  }
  const savedRender = (recordChanges, gameChanges={}) => renderToStaticMarkup(createElement(Game,{...props,detailOnly:true,initialDetailTab:'markets',g:{...g,...gameChanges,market_prediction_record:{...record,...recordChanges}}}));
  assert.match(savedRender({}, {options:[]}),/보존된 경기 전 추천/);
  assert.match(savedRender({fixture:undefined,market_recommendation_options:[]}),/보존된 경기 전 추천/);
  assert.doesNotMatch(savedRender({fixture:undefined}, {options:[]}),/보존된 경기 전 추천/);
  assert.doesNotMatch(savedRender({kickoff:'2099-09-07T09:00:00Z'}),/보존된 경기 전 추천/);
  const keyed = {o1:{result:'hit',market:'언더오버',line:2.5,odds:'1.9',net_units:'0.9'}};
  assert.match(savedRender({market_recommendation_results:keyed}),/순손익 \+0\.90단위/);
  assert.doesNotMatch(savedRender({prediction_snapshot_id:null}, {_decisionFrozen:true}),/보존된 경기 전 추천/);
  assert.match(savedRender({market_recommendation_results:{o1:{...keyed.o1,result:'miss',net_units:'-1'}}}),/미적중 · 순손익 -1\.00단위/);
  assert.match(savedRender({market_recommendation_results:{o1:{...keyed.o1,result:'void',net_units:'0'}}}),/무효 · 순손익 0\.00단위/);
  assert.match(savedRender({market_recommendation_results:{}}),/결과 대기/);
  assert.doesNotMatch(savedRender({fixture:{...fixture,home:'다른 팀'}}),/보존된 경기 전 추천/);
  assert.doesNotMatch(savedRender({market_recommendation_options:[]}, {options:options.map(o=>({...o,offer_id:'changed'}))}),/보존된 경기 전 추천/);
  assert.doesNotMatch(savedRender({captured_at:'2099-09-06T08:45:00Z'}),/보존된 경기 전 추천/);
  assert.doesNotMatch(savedRender({market_recommendation_results:[{...record.market_recommendation_results[0],offer_id:'other'}]}),/순손익/);
  assert.doesNotMatch(savedRender({market_recommendation_results:[{...record.market_recommendation_results[0],net_units:'900'}]}),/순손익/);
  Date.now=()=>Date.parse('2099-09-06T00:00:00Z');
  for(const extra of [{stale:true},{wait:true},{g:{...g,_liveOddsChanged:true}}]){
    const html=renderToStaticMarkup(createElement(Game,{...props,...extra}));assert.doesNotMatch(html,/경기 추천 1픽|언더오버 U 2\.5 · 오버|핸디캡 H -1\.5 · 핸디홈/);
  }
 } finally {Date.now=now;await server.close();}
});
