import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeMatchDetail,readMatchJson,shouldRefreshDetailList} from './match-data.js';
import {recommendationResults} from './recommendation-results.js';

test('detail never overwrites repriced options or frozen revision',()=>{
  const summary={_detail_key:'k',year:2026,round:1,sport:'bs',league:'KBO',date:'09.22',home:'H',away:'A',options:[{price:2}],prediction_record:{revision:'frozen'}};
  const response={revision:'r',game:{...summary,options:[],prediction_record:null,선발:{history:[1]}}};
  const merged=mergeMatchDetail(summary,response,'r');
  assert.deepEqual(merged.options,summary.options);
  assert.deepEqual(merged.prediction_record,summary.prediction_record);
  assert.deepEqual(merged.선발,{history:[1]});
  assert.equal(mergeMatchDetail(summary,response,'new'),null);
  assert.equal(mergeMatchDetail(summary,{...response,game:{...response.game,round:2}},'r'),null);
});

test('old collector route fallback only for 404, not errors',async t=>{
  const urls=[];
  t.mock.method(globalThis,'fetch',async url=>{urls.push(url);return urls.length===1?{status:404}:{ok:true,json:async()=>({live:[]})};});
  assert.deepEqual(await readMatchJson('/api/matches?scope=recent'),{live:[]});
  assert.match(urls[1],/\/api\/picks\?/);
  t.mock.method(globalThis,'fetch',async()=>({status:503}));
  await assert.rejects(readMatchJson('/api/matches?scope=recent'),/503/);
});

test('modal gets comparison rows only for the same evidence and frozen snapshot', () => {
  const compact = {pick_comparison_version:"exact-market-frontier-v1", evidence_hash:"e", items:[{offer_id:"o"}]};
  const full = {...compact, items:[{offer_id:"o", comparisons:{frontier_consensus:{odds:2}}}]};
  const record = {prediction_snapshot_id:"p", market_recommendations:compact, result:"hit"};
  const summary = {_detail_key:"k", year:2026, round:1, sport:"bs", league:"KBO", date:"09.22", home:"H", away:"A",
    market_recommendations:compact, market_prediction_record:record};
  const detail = {...summary, market_recommendations:full,
    market_prediction_record:{...record, result:"pending", market_recommendations:full}};
  const merged = mergeMatchDetail(summary, {revision:"r", game:detail}, "r");
  assert.equal(merged.market_recommendations, full);
  assert.equal(merged.market_prediction_record.market_recommendations, full);
  assert.equal(merged.market_prediction_record.result, "hit");
  const wrong = {...detail, market_recommendations:{...full, evidence_hash:"other"},
    market_prediction_record:{...detail.market_prediction_record, prediction_snapshot_id:"other"}};
  const rejected = mergeMatchDetail(summary, {revision:"r", game:wrong}, "r");
  assert.equal(rejected.market_recommendations, compact);
  assert.equal(rejected.market_prediction_record, record);
});

test('stale detail revision refreshes list once per path without bypassing revision checks',async t=>{
  t.mock.method(globalThis,'fetch',async()=>({status:409,ok:false}));
  const path='/api/match-detail?key=k&revision=old';
  await assert.rejects(readMatchJson(path),error=>error.status===409);
  assert.equal(shouldRefreshDetailList(path,409,null),true);
  assert.equal(shouldRefreshDetailList(path,409,path),false);
  assert.equal(shouldRefreshDetailList(path,503,null),false);
  assert.equal(shouldRefreshDetailList(null,409,null),false);
  assert.equal(shouldRefreshDetailList('/api/matches?scope=recent',409,null),false);
  assert.equal(shouldRefreshDetailList(path.replace('old','new'),409,path),true);
});

test('recent result settlement uses compact history even when list is today-only',()=>{
  const entry={id:'e',recommended:true,kickoff_at:'2026-09-01T12:00:00Z',published_at:'2026-09-01T10:00:00Z',recorded_at:'2026-09-01T10:00:00Z',home:'H',away:'A',sport:'bs',league:'KBO',date:'09.01',round:1,market:'승패',sel:'홈',odds:1.8};
  const old={...entry,prediction_record:{selection_id:'s',market:'승패',selection:'홈',odds:1.8},options:[{selection_id:'s',적중:true}]};
  const today={recommendation_history:{e:entry}}, now=Date.parse('2026-09-22T00:00:00Z');
  const full=recommendationResults(today,{past:[old]},null,now);
  const compact=recommendationResults(today,{live:[],past:[],result_games:[old]},null,now);
  assert.equal(compact.hit,1);assert.deepEqual(compact,full);
});
