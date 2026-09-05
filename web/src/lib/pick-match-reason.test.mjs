import assert from "node:assert/strict";
import test from "node:test";
import { pickMatchReason } from "./pick-match-reason.js";

const now = Date.parse("2026-09-05T03:00:00Z");
const home = { market: "승패", 선택: "홈" };
const base = { home: "요미우리", away: "한신", sport: "bs", status: "경기전",
  year: 2026, date: "09.05(토) 18:00",
  form_home: { last10: "7승 3패", avg_scored: 5.2, avg_conceded: 3.1 },
  form_away: { last10: "4승 6패", avg_scored: 3.8, avg_conceded: 4.3 } };

test("actual records explain the selected side without selection-policy copy", () => {
  const result = pickMatchReason(base, home, now);
  assert.match(result.reason, /요미우리 최근 10경기 7승 3패/);
  assert.match(result.reason, /한신 최근 10경기 4승 6패/);
  assert.match(result.reason, /5.2득점·3.1실점/);
  assert.match(result.reason, /요미우리 쪽 선택을 뒷받침/);
  assert.doesNotMatch(result.reason, /55%|60%|기본 추천|유효 후보|기준을 통과/);
});
test("opposing records never become invented support for the away choice", () => {
  const result = pickMatchReason(base, { ...home, 선택: "원정" }, now);
  assert.equal(result.status, "opposing");
  assert.match(result.reason, /오히려 요미우리 쪽에 유리/);
  assert.match(result.reason, /한신 선택을 경기력 우위로 설명하기 어렵/);
});
test("contradictory attack and recent-result evidence stays visible", () => {
  const game = { ...base, form_home: { ...base.form_home, avg_scored: 2, avg_conceded: 5 } };
  const result = pickMatchReason(game, home, now);
  assert.match(result.reason, /7승 3패/);
  assert.match(result.counterReason, /2.0득점·5.0실점/);
});
test("NPB season records can explain a game without fabricated recent form", () => {
  const game = { ...base, form_home: null, form_away: null,
    선발: { updated_at: "2026-09-05T11:00:00+09:00", teams: {
      home: { wins: 65, losses: 55, draws: 2 }, away: { wins: 55, losses: 62, draws: 3 },
    } } };
  const result = pickMatchReason(game, home, now);
  assert.match(result.reason, /시즌 누적 성적.*65승 2무 55패.*55승 3무 62패/);
  assert.doesNotMatch(result.reason, /최근 10경기/);
  assert.match(result.counterReason, /선발투수 자료가 모두 확인되지는/);
});
test("starter ERA comparisons require matched statistical scope", () => {
  const info = {
    home_detail: { name: "홈선발", stats: { era: 2.4, season: 2026 } },
    away_detail: { name: "원정선발", stats: { era: 4.2, season: 2026 } },
  };
  const game = { ...base, form_home: null, form_away: null, 선발: info };
  assert.match(pickMatchReason(game, home, now).reason, /홈선발 ERA 2.40.*원정선발 ERA 4.20/);
  assert.equal(pickMatchReason(game, home, now).status, "supporting");
  const mixed = { ...game, 선발: { ...info, away_detail: { ...info.away_detail, stats: { era: 4.2 } } } };
  assert.equal(pickMatchReason(mixed, home, now).status, "context");
  const differentPeriods = { ...game, 선발: {
    home_detail: { ...info.home_detail, stats: { ...info.home_detail.stats, period: "최근 5경기" } },
    away_detail: { ...info.away_detail, stats: { ...info.away_detail.stats, period: "2026시즌" } },
  } };
  assert.equal(pickMatchReason(differentPeriods, home, now).status, "context");
});
test("missing, blank, boolean and incomplete records do not manufacture statistics", () => {
  for (const form of [null, {}, { last10: "0승 0패" }, { avg_scored: true, avg_conceded: " " }]) {
    assert.equal(pickMatchReason({ ...base, form_home: form, form_away: form }, home, now).status, "missing");
  }
  assert.equal(pickMatchReason(null, home, now).status, "missing");
});
test("small samples and measured zero are not inflated or removed", () => {
  const game = { ...base, sport: "sc", form_home: { last10: "1승 1무 0패", avg_scored: 1, avg_conceded: 0 },
    form_away: { last10: "0승 0무 2패", avg_scored: 0, avg_conceded: 1 } };
  const result = pickMatchReason(game, home, now);
  assert.match(result.reason, /최근 2경기/);
  assert.match(result.reason, /0.0실점/);
  assert.doesNotMatch(result.reason, /최근 10경기/);
});
test("totals discuss the actual line but not a fabricated expected score", () => {
  const result = pickMatchReason(base, { market: "언더오버", 선택: "언더", line: 7.5 }, now);
  assert.match(result.reason, /9.0점.*7.5점 언더/);
  assert.match(result.reason, /예상 총득점이나 적중 확률을 뜻하지/);
  assert.doesNotMatch(result.reason, /쪽 선택을 뒷받침/);
});
for (const option of [
  { market: "핸디캡", label: "H -1.5", 선택: "핸디홈" },
  { market: "전반언더오버", label: "U/O 1.5", 선택: "언더", line: 1.5 },
  { market: "승무패", 선택: "무" },
]) test(`${option.market}/${option.선택} is not explained as a full-time team win`, () => {
  const result = pickMatchReason(base, option, now);
  assert.match(result.reason, /조건의 적중을 뒷받침할 수는 없/);
  assert.doesNotMatch(result.reason, /쪽 선택을 뒷받침|두 평균의 합/);
});
test("post-start, stale terminal status and later context cannot explain a pregame pick", () => {
  for (const patch of [{ _liveStarted: true }, { status: "정산" }, { date: "09.04(금) 18:00" }, { date: "" }]) {
    assert.equal(pickMatchReason({ ...base, ...patch }, home, now).status, "missing");
  }
  const game = { ...base, form_home: null, form_away: null, 선발: {
    updated_at: "2026-09-05T19:00:00+09:00", teams: { home: { wins: 10, losses: 2 }, away: { wins: 2, losses: 10 } },
  } };
  assert.equal(pickMatchReason(game, home, now).status, "missing");
});
test("volleyball does not guess the unit of legacy recent-score fields", () => {
  assert.ok(pickMatchReason({ ...base, sport: "vl" }, home, now).evidence.every((row) => row.kind !== "recent_balance"));
});
