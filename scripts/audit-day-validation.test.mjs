import assert from 'node:assert/strict';
import test from 'node:test';
import {auditDay,validHistory,kstDay} from './audit-day-validation.mjs';

const now=Date.parse('2026-09-08T22:17:15+09:00');
const row=(i=0)=>({id:'r'+i,home:'LG'+i,away:'키움',sport:'bs',league:'KBO',round:106,
  date:'09.08(화) 18:30',game_no:String(i),market:'전반핸디캡',market_label:'h H -1.5',
  sel:'전반핸디원정',n_way:2,odds:1.52,probability:.5897,recommended:true,
  kickoff_at:'2026-09-08T18:30:00+09:00',published_at:'2026-09-08T17:54:23+09:00',recorded_at:'2026-09-08T08:54:23Z'});
test('KST midnight and strict pre-T30 timestamps',()=>{
  assert.equal(kstDay('2026-09-07T15:01:00Z'),'2026-09-08');
  assert.equal(validHistory(row(),'2026-09-08',now),true);
  for(const change of [{recorded_at:'2026-09-08T09:00:00Z'},{published_at:'bad'},
    {recommended:null},{kickoff_at:'2026-09-09T18:30:00+09:00'}])
    assert.equal(validHistory({...row(),...change},'2026-09-08',now),false);
});
test('audit grades all history entries beyond UI ten-item limit and preserves membership',()=>{
  const history=Object.fromEntries(Array.from({length:13},(_,i)=>['r'+i,{...row(i),recommended:i<12}]));
  const markets=Object.fromEntries(Object.values(history).map(r=>[r.game_no,{...r,label:r.market_label,result:'핸디패'}]));
  const report=auditDay({live:[],past:[]},{games:[]},{markets:{106:markets}},
    {recommendation_history:history},'2026-09-08',now);
  assert.equal(report.recommended.metrics.hit,12);
  assert.equal(report.frozenCandidates.metrics.hit,13);
  assert.equal(history.r0.result,undefined);
  assert.equal(history.r12.recommended,false);
  assert.equal(report.completedGames.recommended.total,0,'official market result is not evidence of full-time finish');
});
test('bad dates and duplicate event copies fail closed',()=>{
  const run=(history,day='2026-09-08')=>auditDay({},{games:[]},{},{recommendation_history:history},day,now);
  assert.throws(()=>run({},'2026-02-30'));
  assert.throws(()=>run({a:row(),b:{...row(),id:'b'}}));
});
