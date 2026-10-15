import test from 'node:test';
import assert from 'node:assert/strict';
import {rankTodayPicks} from './today-probability-ranking.js';
import {selectionKey} from './unified-recommendation.js';

const now=Date.parse('2026-09-22T10:00:00+09:00');
function fixture(id,p=.6,date='09.22(화) 18:00') {
  const option={selection_id:id,게임번호:id,market:'승패',선택:'홈',배당:1.6,시장확률:p};
  const game={year:2026,round:112,sport:'bs',league:'KBO',home:id,away:'원정',date,status:'경기전',options:[option]};
  return {game,key:selectionKey(option,112),membership:{recommended:true,selection:{predicted_hit_prob:p}}};
}
function rank(fixtures,options={}) {
  return rankTodayPicks(fixtures.map(x=>x.game),new Map(fixtures.map(x=>[x.key,x.membership])),{now,...options});
}
test('descending probability, no input mutation, not price-first',()=>{
  const a=fixture('a',.6),b=fixture('b',.7);a.game.options[0].배당=2;
  const before=JSON.stringify([a,b]);
  assert.deepEqual(rank([a,b]).map(x=>x.game.home),['b','a']);
  assert.equal(JSON.stringify([a,b]),before);
});
test('excludes unhighlighted, wrong date, started, stale, invalid and changed prices',()=>{
  const no=fixture('no');no.membership.recommended=false;
  const changed=fixture('changed');changed.game._liveOddsChanged=true;
  const bad=fixture('bad',NaN);
  const rows=[no,changed,bad,fixture('tomorrow',.8,'09.23(수) 18:00'),fixture('started',.9,'09.22(화) 09:00')];
  assert.equal(rank(rows).length,0);
  assert.equal(rank([fixture('valid')],{stale:true}).length,0);
});
test('frozen record probability and odds are preserved; mismatch excluded',()=>{
  const a=fixture('a',.8,'09.22(화) 10:20');
  a.game.prediction_record={selection_id:'a',probability:.56,odds:1.7};
  assert.equal(rank([a])[0].probability,.56);assert.equal(rank([a])[0].odds,1.7);
  a.game.prediction_record.selection_id='other';assert.equal(rank([a]).length,0);
});
test('duplicates, ties and KST midnight are deterministic',()=>{
  const a=fixture('a'),b=fixture('b');
  assert.deepEqual(rank([b,a,a]).map(x=>x.game.home),['a','b']);
  assert.equal(rank([a],{now:Date.parse('2026-09-21T15:00:00Z')}).length,1);
  assert.equal(rank([a],{now:Date.parse('2026-09-22T15:00:00Z')}).length,0);
});
