import assert from "node:assert/strict";
import test from "node:test";
import { trackTodayPicks } from "./today-pick-tracking.js";

const now = Date.parse("2026-09-05T07:00:00Z");
const record = { selection_id: "old-pick", market: "승패", label: "", selection: "홈",
  probability: .5607, odds: 1.59, captured_at: "2026-09-05T03:00:00Z" };
const game = (changes = {}) => ({ year: 2026, date: "09.05(토) 14:00", round: 105,
  league: "NPB", sport: "bs", home: "오릭스", away: "상대팀", options: [],
  prediction_record: { ...record }, _liveFeedAt: new Date(now).toISOString(),
  _liveState: { status: "STARTED", status_text: "6회초", home_score: 4, away_score: 1 },
  ...changes });
const candidate = (changes = {}) => ({ year: 2026, date: "09.05(토) 14:00", round: 105,
  game_no: "10", home: "오릭스", away: "상대팀", league: "NPB", sport: "bs",
  kickoff_at: "2026-09-05T14:00:00+09:00", market: "승패", sel: "홈", market_label: "",
  market_prob: .5607, predicted_hit_prob: .5607, odds: 1.59, ...changes });
const track = (games, today = { candidates: [] }, time = now) => trackTodayPicks({ games, today, now: time });

test("missing started NPB candidates recover original Orix and Softbank records", () => {
  const games = [game(), game({ home: "소프트뱅크", prediction_record: {
    ...record, selection_id: "softbank", probability: .5897, odds: 1.52,
  } })];
  const before = structuredClone(games);
  const rows = track(games);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map((r) => [r.openingProbability, r.originalOdds]).sort(), [[.5607, 1.59], [.5897, 1.52]]);
  assert.ok(rows.every((r) => r.source === "recorded" && r.estimate?.probability > r.openingProbability));
  assert.deepEqual(games, before);
});

test("record persists through live, pending feed and final result with original odds", () => {
  const live = game();
  const ended = { ...live, _liveState: { ...live._liveState, finished: true } };
  const stalled = { ...live, _liveFeedAt: "2026-09-05T05:00:00Z" };
  for (const g of [live, stalled, ended]) {
    const [row] = track([g]);
    assert.equal(row.openingProbability, .5607);
    assert.equal(row.originalOdds, 1.59);
  }
  assert.equal(track([ended])[0].outcome.state, "hit");
  assert.equal(track([ended])[0].estimate, null);
  assert.equal(track([stalled])[0].estimate, null);
  assert.match(track([stalled])[0].estimateMessage, /갱신이 늦어/);
});

test("postgame/current prices cannot fabricate missing or late pregame records", () => {
  const today = { generated_at: "2026-09-05T06:00:00Z", candidates: [candidate({ odds: 1.1, market_prob: .95 })] };
  for (const prior of [null, { ...record, captured_at: "2026-09-05T05:00:00Z" },
    { ...record, captured_at: "2026-09-05T06:00:00Z" }, { ...record, captured_at: "bad" }]) {
    assert.deepEqual(track([game({ prediction_record: prior })], today), []);
  }
});

test("known highlight needs pregame timestamp and same recorded selection/price", () => {
  const c = candidate({ daily_recommendation: { recommended: true } });
  assert.equal(track([game()], { candidates: [c], generated_at: "2026-09-05T04:00:00Z" })[0].source, "highlight");
  assert.equal(track([game()], { candidates: [c], generated_at: "2026-09-05T06:00:00Z" })[0].source, "recorded");
  assert.equal(track([game()], { candidates: [{ ...c, odds: 1.7 }], generated_at: "2026-09-05T04:00:00Z" })[0].source, "recorded");
  assert.equal(track([game()], { candidates: [{ ...c, sel: "원정" }], generated_at: "2026-09-05T04:00:00Z" })[0].source, "recorded");
  assert.equal(track([game()], { candidates: [{ ...c, recommended_at: "2026-09-05T04:00:00Z" }],
    generated_at: "2026-09-05T06:00:00Z" })[0].source, "recorded");
});

test("current reranking never rewrites a recovered record or claims old membership", () => {
  const changed = candidate({ sel: "원정", odds: 1.2, market_prob: .9 });
  const [row] = track([game({ options: [{ 선택: "원정", 배당: 1.2, 시장확률: .9 }] })],
    { generated_at: "2026-09-05T06:00:00Z", candidates: [changed] });
  assert.equal(row.option.선택, "홈");
  assert.equal(row.source, "recorded");
  assert.equal(row.originalOdds, 1.59);
});

test("actual KST today excludes yesterday, tomorrow and a same date in another year", () => {
  const games = [game(), game({ date: "09.04(금) 14:00" }), game({ date: "09.06(일) 14:00" }), game({ year: 2025 })];
  assert.equal(track(games).length, 1);
  const nextDay = track(games, null, Date.parse("2026-09-05T15:01:00Z"));
  assert.equal(nextDay.length, 1);
  assert.equal(nextDay[0].game.date, "09.06(일) 14:00");
});

test("future generic priors are not promoted into the current recommendation roster", () => {
  assert.deepEqual(track([game({ date: "09.05(토) 18:00", _liveState: null })]), []);
});

test("raw rounds deduplicate exact event/selection using oldest saved prior, not new prices", () => {
  const old = game({ round: 104 });
  const newer = game({ round: 105, prediction_record: { ...record, odds: 1.8,
    probability: .61, captured_at: "2026-09-05T04:00:00Z" } });
  for (const games of [[old, newer], [newer, old]]) {
    const rows = track(games);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].originalOdds, 1.59);
    assert.equal(rows[0].openingProbability, .5607);
  }
  assert.equal(track([old, game({ prediction_record: { ...record, selection: "원정" } })]).length, 2);
  assert.equal(track([old, game({ date: "09.05(토) 15:00" })]).length, 2);
});

test("prestart without record requires exact current option, valid source snapshot and roster", () => {
  const early = Date.parse("2026-09-05T04:00:00Z");
  const option = { market: "승패", label: "", 선택: "홈", 배당: 1.59, 시장확률: .5607 };
  const g = game({ prediction_record: null, _liveState: null, options: [option] });
  const today = { generated_at: "2026-09-05T03:00:00Z", candidates: [candidate()] };
  const [row] = track([g], today, early);
  assert.equal(row.source, "current");
  assert.equal(row.originalOdds, 1.59);
  assert.equal(row.openingProbability, .5607);
  assert.equal(track([{ ...g, options: [{ ...option, 시장확률: .560733 }] }], today, early).length, 1);
  assert.deepEqual(track([{ ...g, options: [{ ...option, 시장확률: .61 }] }], today, early), []);
  assert.deepEqual(track([{ ...g, options: [{ ...option, 배당: 1.7 }] }], today, early), []);
  assert.deepEqual(track([{ ...g, _liveOddsChanged: true }], today, early), []);
  assert.deepEqual(track([g], { ...today, generated_at: "2026-09-05T06:00:00Z" }, early), []);
  assert.deepEqual(track([g], today, now), []);
});

test("unsupported estimates and incomplete data retain known original picks", () => {
  const unsupported = game({ prediction_record: { ...record, market: "핸디캡", label: "H -1.5", selection: "핸디홈" } });
  const [row] = track([unsupported]);
  assert.equal(row.estimate, null);
  assert.match(row.estimateMessage, /지원하지/);
  const missing = game({ prediction_record: { ...record, probability: null } });
  const [known] = track([missing], { generated_at: "2026-09-05T04:00:00Z",
    candidates: [candidate({ daily_recommendation: { recommended: true } })] });
  assert.equal(known.openingProbability, null);
  assert.equal(known.estimate, null);
  assert.equal(known.originalOdds, 1.59);
});

test("unsafe priors and explicitly excluded original highlights are not recovered", () => {
  assert.deepEqual(track([game({ prediction_record: { ...record, market: "홀짝", selection: "홀" } })]), []);
  assert.deepEqual(track([game({ prediction_record: { ...record, odds: 2.3 } })]), []);
  assert.deepEqual(track([game()], { generated_at: "2026-09-05T04:00:00Z",
    candidates: [candidate({ daily_recommendation: { recommended: false } })] }), []);
});
