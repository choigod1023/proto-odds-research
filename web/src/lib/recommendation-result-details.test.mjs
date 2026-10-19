import test from 'node:test';
import assert from 'node:assert/strict';
import {officialScore,resultExplanation} from './recommendation-result-details.js';
const row=(extra={})=>({kickoff_at:'2026-10-17T14:00:00+09:00',sport:'bs',market:'승패',sel:'홈',outcome:{state:'hit'},
  official_score:{kickoff_at:'2026-10-17T14:00:00+09:00',source:'proto_full_game_result',score:[5,1]},...extra});
test('win/loss, handicap and total explain the original pick against official settlement',()=>{
  assert.match(resultExplanation(row()),/홈 5 : 원정 1.*정산 선택 홈.*적중/);
  assert.match(resultExplanation(row({sel:'원정',outcome:{state:'miss'}})),/당시 선택 원정 → 실패/);
  assert.match(resultExplanation(row({market:'핸디캡',market_label:'H -2.5',sel:'핸디홈',official_market_result:'핸디승',n_way:2})),/2.5 : 1.*적중/);
  assert.match(resultExplanation(row({market:'언더오버',market_label:'U 7.5',sel:'오버',official_market_result:'언더',outcome:{state:'miss'}})),/합계 5 \+ 1 = 6.*미만.*실패/);
  assert.doesNotMatch(resultExplanation(row({sport:'vl',market:'언더오버',market_label:'U 180.5',sel:'오버',official_market_result:'오버'})),/합계/);
  assert.match(resultExplanation(row({outcome:{state:'miss'}})),/점수만으로 판정 사유를 확정할 수 없어/);
});
test('rejects malformed, adjusted or wrong-event score evidence',()=>{
  for(const evidence of [null,{source:'live',score:[5,1]}, {...row().official_score,score:[null,1]}, {...row().official_score,score:[-1,1]}, {...row().official_score,kickoff_at:'2026-10-18T14:00:00+09:00'}])
    assert.equal(officialScore(row({official_score:evidence})),null);
});

test('legacy official settlements explain arithmetic only when the saved outcome agrees',()=>{
  const over=row({market:'언더오버',market_label:'U 4.5',sel:'오버',outcome:{state:'miss'},
    official_score:{...row().official_score,score:[0,1]}});
  assert.match(resultExplanation(over),/합계 0 \+ 1 = 1, 기준 4.5 미만.*실패/);
  assert.match(resultExplanation({...over,outcome:{state:'hit'}}),/점수만으로 판정 사유를 확정할 수 없어/);
  assert.doesNotMatch(resultExplanation({...over,sport:'vl'}),/합계/);
  assert.match(resultExplanation(row({market:'핸디캡',market_label:'H -2.5',sel:'핸디홈',n_way:2})),/2.5 : 1.*적중/);
});
