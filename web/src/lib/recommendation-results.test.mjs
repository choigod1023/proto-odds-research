import test from 'node:test';
import assert from 'node:assert/strict';
import { recommendationResults } from './recommendation-results.js';
const now=Date.parse('2026-09-06T15:00:00+09:00');
const row=(id='1',extra={})=>({id,home:'H',away:'A',sport:'bs',league:'MLB',date:'09.06(일) 12:00',round:1,game_no:id,
  kickoff_at:'2026-09-06T12:00:00+09:00',published_at:'2026-09-06T09:00:00+09:00',recorded_at:'2026-09-06T09:00:00+09:00',
  market:'승패',market_label:'',sel:'홈',odds:1.6,recommended:true,result:'hit',result_source:'official',...extra});
test('unknown membership and post-freeze captures never enter denominator',()=>{
  assert.equal(recommendationResults(null,{live:[{prediction_record:{result:'hit'}}]},null,now).settled.length,0);
  const history={a:row('a'),b:row('b',{recommended:false}),c:row('c',{recorded_at:'2026-09-06T11:30:00+09:00'})};
  assert.equal(recommendationResults({recommendation_history:history},null,null,now).settled.length,1);
});
test('latest ten chosen by kickoff regardless of outcome; void and pending separate',()=>{
  const entries=Array.from({length:12},(_,i)=>row(String(i),{kickoff_at:`2026-09-06T12:${String(i).padStart(2,'0')}:00+09:00`,result:i<7?'hit':'miss'}));
  entries.push(row('void',{result:'void'}),row('pending',{result:'pending'}),row('future',{kickoff_at:'2026-09-06T20:00:00+09:00'}));
  const r=recommendationResults({recommendation_history:Object.fromEntries(entries.map(x=>[x.id,x]))},null,null,now);
  assert.equal(r.settled.length,10);assert.equal(r.hit,5);assert.equal(r.settled[0].id,'11');
  assert.equal(r.pending,1);assert.equal(r.upcoming,1);assert.equal(r.void,1);
});
test('official offer result can update archive; wrong line and score cannot',()=>{
  const entry=row('1',{result:undefined,result_source:undefined});
  const today={recommendation_history:{a:entry}};
  const official={home:'H',away:'A',date:entry.date,market:'승패',label:'',game_no:'1',n_way:2,result:'홈패'};
  const odds={markets:{1:{1:official}}};
  assert.equal(recommendationResults(today,null,odds,now).settled[0].outcome.state,'miss');
  official.label='different';
  assert.equal(recommendationResults(today,{live:[{...entry,status:'정산',score:[5,1]}]},odds,now).settled.length,0);
});
