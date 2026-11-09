import assert from 'node:assert/strict';
import test from 'node:test';
import { MATCH_LIST_REFRESH_MS, matchRefreshNotice, outcomeDisplayLabel } from './match-refresh-notice.js';
import { repriceGameOdds } from './live-odds.js';

const now = Date.parse('2026-11-07T03:00:00Z');
const game = {year:2026, round:132, sport:'bs', league:'KBO', home:'삼성', away:'KT', date:'11.07(토) 14:00',
  options:[{market:'승패', label:'', 게임번호:'1', 선택:'홈', 배당:1.8},
           {market:'승패', label:'', 게임번호:'1', 선택:'원정', 배당:1.9}]};

test('a real live reprice explains publication and automatic polling instead of an ETA', () => {
  const next = repriceGameOdds(game, {'1':[1.7, 2.1]}, '2026-11-07T02:59:00Z');
  assert.equal(next.prediction_status, 'prediction_ledger_required');
  assert.equal(next.prediction_record, null);
  const notice = matchRefreshNotice(next, {now});
  assert.equal(MATCH_LIST_REFRESH_MS, 60000);
  assert.equal(notice.kind, 'recalculating');
  assert.equal(notice.previous, null);
  assert.match(notice.body, /사전 원장 저장 → 경기 목록 게시/);
  assert.match(notice.followUp, /약 1분 간격/);
  assert.match(notice.followUp, /완료 시각은 확정할 수 없습니다/);
  assert.equal(notice.observedAt, '2026-11-07T02:59:00Z');
  assert.equal(notice.freezeAt, '2026-11-07T04:30:00.000Z');
});

test('recalculation shows only the recorded previous price and capture time', () => {
  const recorded = { ...game, featured_pick_record: {
    captured_at: '2026-11-07T02:20:00Z',
    kickoff: '2026-11-07T05:00:00Z',
    prediction_snapshot_id: 'stored-1',
    pick: {version:'featured-pregame-v1', selection_id:'s1', offer_id:'o1',
      market:'언더오버', label:'U 7.5', selection:'언더', odds:1.82,
      probability:.58, estimated_roi:.0556},
  }};
  const next = repriceGameOdds(recorded, {'1':[1.7, 2.1]}, '2026-11-07T02:59:00Z');
  assert.equal(next.options[0].배당, 1.7);
  assert.deepEqual(matchRefreshNotice(next, {now}).previous, {
    capturedAt: '2026-11-07T02:20:00Z',
    option: {market:'언더오버', label:'U 7.5', 선택:'언더', 배당:1.82},
  });
  assert.equal(matchRefreshNotice({...next, prediction_ledger_reason:'ledger_conflict'}, {now}).previous, undefined);
  assert.equal(matchRefreshNotice({...next, featured_pick_record:{
    ...recorded.featured_pick_record, captured_at:'2026-11-07T02:59:30Z',
  }}, {now}).previous, null);
  assert.equal(matchRefreshNotice({...next, featured_pick_record:{
    ...recorded.featured_pick_record, kickoff:'2026-11-08T05:00:00Z',
  }}, {now}).previous, null);
});

test('ready picks and normal abstentions have no recalculation notice', () => {
  assert.equal(matchRefreshNotice(game, {now}), null);
  assert.equal(matchRefreshNotice({...game, prediction_status:'recorded_withhold'}, {now}), null);
  assert.equal(matchRefreshNotice(null), null);
});

test('a frozen or conflicting pick is not promised a future replacement', () => {
  const waiting = {...game, _liveOddsChanged:true};
  const frozen = matchRefreshNotice(waiting, {now:Date.parse('2026-11-07T04:30:00Z')});
  assert.equal(frozen.kind, 'frozen');
  assert.doesNotMatch(frozen.body, /끝나면|자동 확인/);
  const blocked = matchRefreshNotice({...waiting, prediction_ledger_reason:'ledger_conflict'}, {now});
  assert.equal(blocked.kind, 'blocked');
  assert.match(blocked.body, /원장 연결이 복구/);
});

test('malformed observation timestamps are omitted', () => {
  const notice = matchRefreshNotice({...game, _liveOddsChanged:true, _liveOddsRecalculatedAt:'broken'}, {now});
  assert.equal(notice.observedAt, null);
});

test('ended picks distinguish official result waiting from ledger publication waiting', () => {
  const pending = {...game, status:'정산', prediction_status:'prediction_ledger_required',
    prediction_record:{selection_id:'home', market:'승패', label:'', selection:'홈', result:'pending'}};
  const before = JSON.stringify(pending);
  const notice = matchRefreshNotice(pending, {now});
  assert.equal(notice.kind, 'settlement');
  assert.equal(notice.title, '경기 종료 · 공식 상품 정산 대기');
  assert.match(notice.followUp, /실현 ROI 계산에서 제외/);
  assert.equal(JSON.stringify(pending), before);
  const official = {...pending, _officialMarkets:{'1':{
    home:game.home, away:game.away, date:game.date, market:'승패', label:'', game_no:'1', n_way:2, result:'홈승'}}};
  assert.equal(matchRefreshNotice(official, {now}).title, '공식 결과 확인 · 성적 집계 반영 대기');
  assert.equal(matchRefreshNotice({...pending, prediction_record:{...pending.prediction_record, result:'hit'}}, {now}), null);
});

test('partially settled independent picks remain visible without hiding already settled winner', () => {
  const pending = {...game, status:'정산', prediction_record:{result:'hit'},
    featured_pick_record:{result:null}, market_prediction_record:{market_recommendations:{items:[{offer_id:'a'},{offer_id:'b'}]},
      market_recommendation_results:{a:{result:'hit'}}}};
  const notice = matchRefreshNotice(pending, {now});
  assert.match(notice.body, /경기 추천 1픽 · 시장별 픽/);
  assert.doesNotMatch(notice.body, /승패·승무패 픽/);
  assert.equal(notice.title, '경기 종료 · 공식 상품 정산 대기');
  assert.equal(matchRefreshNotice({...game, status:'정산'}, {now}), null);
});

test('score-derived hits remain provisional; official/ledger grades keep their final label', () => {
  assert.equal(outcomeDisplayLabel({source:'score', state:'hit', label:'적중'}), '점수 기준 잠정 적중');
  assert.equal(outcomeDisplayLabel({source:'score', state:'miss', label:'적중실패'}), '점수 기준 잠정 적중실패');
  for (const source of ['ledger','official']) {
    assert.equal(outcomeDisplayLabel({source, state:'hit', label:'적중'}), '적중');
    assert.equal(outcomeDisplayLabel({source, state:'void', label:'무효'}), '무효');
  }
});
