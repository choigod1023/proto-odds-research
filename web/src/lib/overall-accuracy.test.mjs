import test from 'node:test';import assert from 'node:assert/strict';import {overallAccuracy} from './overall-accuracy.js';
import {accuracyTime} from './overall-accuracy.js';
const record=(id,result='pending')=>({prediction_snapshot_id:id,result,selection_id:id,market:'승패',selection:'홈'});
const data=(records,live=[])=>({prediction_performance:{scope:'all_ledger_predictions',records},live});
test('all-history settled results include predictions outside current game window',()=>{
 const s=overallAccuracy(data({old:record('old','hit'),lost:record('lost','miss'),void:record('void','void'),pending:record('pending')}));
 assert.equal(s.rate,.5);assert.equal(s.settled,2);assert.equal(s.void,1);assert.equal(s.pending,1);
});
test('final score updates once, does not count live scores or duplicate rounds',()=>{
 const r=record('a');const g={sport:'bs',prediction_record:r,_liveState:{finished:false,home_score:3,away_score:1}};
 assert.equal(overallAccuracy(data({a:r},[g])).rate,null);
 g._liveState.finished=true;
 const s=overallAccuracy(data({a:r},[g,g]));assert.equal(s.hit,1);assert.equal(s.provisional,1);
 assert.equal(overallAccuracy(data({a:record('a','miss')},[g])).miss,1);
});
test('unrecorded or different revisions and conflicting duplicates are excluded',()=>{
 const r=record('a');const g={sport:'bs',prediction_record:record('other'),status:'정산',score:[3,1]};
 assert.equal(overallAccuracy(data({a:r},[g])).pending,1);
 const a={...g,prediction_record:r},b={...a,score:[1,3]};assert.equal(overallAccuracy(data({a:r},[a,b])).pending,1);
 assert.equal(overallAccuracy({live:[g]}).total,0);
});
test('empty ledger is not a zero percent success rate',()=>assert.equal(overallAccuracy(data({})).rate,null));
test('sport totals reconcile with every visible row including missing metadata',()=>{
 const s=overallAccuracy(data({a:{...record('a','hit'),fixture:{sport:'bs',home:'KIA',away:'LG'},kickoff:'2026-10-18T08:00:00Z'},b:{...record('b','miss'),fixture:{sport:'sc'}},c:record('c'),d:{...record('d','void'),fixture:{sport:'bs'}}}));
 for(const key of ['total','hit','miss','void','pending','provisional'])assert.equal(s.sports.reduce((sum,g)=>sum+g[key],0),s[key]);
 assert.equal(s.rows.length,s.total);assert.equal(s.sports.find(g=>g.sport==='unknown').pending,1);
 assert.equal(s.rows[0].home,'KIA');assert.equal(s.sports.find(g=>g.sport==='bs').rate,1);
});
test('timestamps are explicit KST and never infer missing or naive times',()=>{
 assert.equal(accuracyTime('2026-10-18T16:05:00Z'),'2026-10-19 01:05');
 assert.equal(accuracyTime('2026-10-18T16:05:00'),'시각 정보 없음');
 assert.equal(accuracyTime(null),'시각 정보 없음');
});
test('different revision does not borrow team metadata',()=>{
 const s=overallAccuracy(data({a:record('a','hit')},[{prediction_record:record('other'),sport:'bs',home:'wrong',away:'wrong'}]));
 assert.equal(s.rows[0].home,null);assert.equal(s.sports[0].sport,'unknown');
});
test('existing pregame records display results before canonical feed and survive transition once',()=>{
 const r={...record('old','hit'),captured_at:'2026-09-01T00:00:00Z'};
 const g={year:2026,sport:'bs',league:'MLB',home:'홈',away:'원정',date:'09.01 12:00',prediction_record:r};
 const prior=overallAccuracy({live:[g],past:[{...g,round:2}]});assert.equal(prior.hit,1);assert.equal(prior.rate,1);
 const next=overallAccuracy({...data({old:{...r,result:'miss'},new:record('new','hit')}),live:[g]});
 assert.equal(next.total,2);assert.equal(next.hit,1);assert.equal(next.miss,1);
 assert.equal(overallAccuracy({live:[{...g,prediction_record:{...r,captured_at:'2026-09-01T04:00:00Z'}}]}).total,0);
});
test('new canonical revision replaces old saved revision for the same event',()=>{
 const g={year:2026,sport:'bs',league:'MLB',home:'홈',away:'원정',date:'09.01 12:00',decision_snapshot:{event_id:'event'},prediction_record:{...record('old','hit'),captured_at:'2026-09-01T00:00:00Z'}};
 const s=overallAccuracy({...data({event:record('new','miss')}),live:[g]});assert.equal(s.total,1);assert.equal(s.miss,1);
});
