import test from 'node:test';import assert from 'node:assert/strict';import {overallAccuracy} from './overall-accuracy.js';
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
 assert.equal(overallAccuracy({live:[g]}),null);
});
test('empty ledger is not a zero percent success rate',()=>assert.equal(overallAccuracy(data({})).rate,null));
