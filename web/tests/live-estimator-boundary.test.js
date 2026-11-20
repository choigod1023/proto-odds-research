import test from 'node:test';
import assert from 'node:assert/strict';
import { estimateLiveProbability } from '../src/lib/bet-ledger.js';
import { savedLivePrediction } from '../src/lib/saved-live-prediction.js';

test('existing LIVE estimate responds to score and clock, not substitution or injury', () => {
  const bet = {openingProbability:.55, game:{sport:'sc'}, selection:{market:'승무패', choice:'홈'}};
  const live = {status:'STARTED',home_score:0,away_score:0,clock:{elapsed_minute:45}};
  const original = estimateLiveProbability(bet,live);
  const withPlayerFacts = estimateLiveProbability(bet,{...live, substitutions:[{player_id:'p1',type:'off'}], injuries:[{player_id:'p2'}]});
  assert.deepEqual(withPlayerFacts,original);
  assert.notEqual(estimateLiveProbability(bet,{...live,home_score:1}).probability,original.probability);
  assert.notEqual(estimateLiveProbability(bet,{...live,home_score:1,clock:{elapsed_minute:75}}).probability,
                  estimateLiveProbability(bet,{...live,home_score:1}).probability);
});

test('saved LIVE display keeps pregame prediction and rejects stale feed', () => {
  const now=Date.parse('2026-09-05T11:00:00Z');
  const game={year:2026,date:'09.05(토) 18:00',sport:'bs',home:'홈팀',away:'원정팀',
    prediction_record:{selection_id:'saved',market:'승패',selection:'홈',odds:1.8,probability:.57,captured_at:'2026-09-05T08:00:00Z'}};
  const live={status:'STARTED',status_text:'6회초',home_score:4,away_score:1,observed_at:'2026-09-05T10:59:00Z'};
  assert.equal(savedLivePrediction(game,live,now).estimateStatus,'available');
  assert.equal(savedLivePrediction(game,{...live,observed_at:'2026-09-05T10:49:00Z'},now).estimateStatus,'stale_live');
  assert.equal(game.prediction_record.probability,.57);
});

test('basketball and volleyball STARTED progress defaults to one half without a clock model', () => {
  const live={status:'STARTED',home_score:2,away_score:1,clock:{elapsed_minute:7}};
  for (const sport of ['bk','vl']) {
    const bet={openingProbability:.55,game:{sport},selection:{market:'승패',choice:'홈'}};
    assert.equal(estimateLiveProbability(bet,live).progress,.5);
  }
});
