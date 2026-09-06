import test from 'node:test';
import assert from 'node:assert/strict';
import { extract, metrics, compare } from './audit-pick-performance.mjs';
const game = (id='a', capture='2026-09-01T00:00:00Z') => ({year:2026,date:'09.01(화) 12:00',sport:'bs',league:'test',home:'A',away:'B',status:'정산',score:[2,1],
  prediction_record:{prediction_snapshot_id:id,captured_at:capture,market:'승패',selection:'홈',odds:1.6,probability:0.6,result:'pending'}});
test('duplicate rows counted once, latest pregame revision wins, postgame excluded',()=>{
  const old=game(), latest=game('b','2026-09-01T01:00:00Z');
  const a=extract({live:[old,old,latest,game('late','2026-09-01T03:00:00Z')],past:[]});
  assert.equal(a.rows.length,1); assert.equal(a.rows[0].id,'b');
  assert.equal(a.checks.duplicateRows,1); assert.equal(a.checks.notPregame,1);
  assert.equal(a.ui.hit,1);
});
test('conflicting score copies remain pending, official overrides score',()=>{
  const a=game(), b={...game(),score:[0,1]};
  assert.equal(extract({live:[a,b]}).rows[0].state,'pending');
  b.prediction_record={...b.prediction_record,selection_id:'s'};
  b.options=[{selection_id:'s',적중:false}];
  const report=extract({live:[a,b]});
  assert.equal(report.rows[0].source,'official'); assert.equal(report.rows[0].state,'miss');
});
test('ledger disagreement with independently available official result is surfaced',()=>{
  const g=game(); g.prediction_record={...g.prediction_record,result:'hit',selection_id:'s'};
  g.options=[{selection_id:'s',적중:false}];
  assert.equal(extract({live:[g]}).checks.ledgerOfficialDisagreements.length,1);
});
test('void/pending excluded, missing odds never invented, original probability used',()=>{
  const r=metrics([{state:'hit',odds:2,probability:0.6},{state:'miss',odds:1.5,probability:0.4},{state:'void',odds:10},{state:'pending'}, {state:'miss',odds:null}]);
  assert.equal(r.settled,3); assert.equal(r.hit,1); assert.equal(r.priced,2);
  assert.equal(r.roi,0); assert.equal(r.probabilityN,2);
  assert.ok(Math.abs(r.brier-0.16)<1e-12); assert.equal(metrics([]).rate,null);
});
test('chronological split and selected cohort do not depend on holdout outcomes',()=>{
  const rows=Array.from({length:10},(_,i)=>({day:`2026-09-${String(i+1).padStart(2,'0')}`,state:i%2?'hit':'pending',probability:0.6,odds:1.6}));
  const a=compare(rows),b=compare(rows.map(r=>({...r,state:'miss'})));
  assert.equal(a.cutoff,'2026-09-08'); assert.equal(a.cutoff,b.cutoff);
  assert.equal(a.policies[0].holdout.total,3);
  for(let i=0;i<a.policies.length;i++) assert.equal(a.policies[i].holdout.total,b.policies[i].holdout.total);
  assert.equal(compare(rows.slice(0,1)).cutoff,null);
});
