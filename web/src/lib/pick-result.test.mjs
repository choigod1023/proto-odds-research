import assert from "node:assert/strict";
import test from "node:test";
import { gamePhase, recommendationOutcome } from "./match-status.js";

const game = (market = "승패", selection = "홈", label = "") => ({
  home: "홈팀", away: "원정팀", date: "09.04(금) 18:00", sport: "bs",
  status: "결과확인", prediction_record: {
    selection_id: "saved-pick", market, selection, label, result: "pending", odds: 1.8,
  },
});
const finalScore = (home, away) => ({
  status: "RESULT", finished: true, home_score: home, away_score: away,
});

test("final live score resolves the saved pick before the slow publisher settles it", () => {
  const g = game();
  assert.equal(recommendationOutcome(g, finalScore(5, 2)).label, "적중");
  assert.equal(recommendationOutcome(g, finalScore(2, 5)).label, "적중실패");
  assert.equal(gamePhase(g, finalScore(5, 2)), "finished");
  assert.equal(g.prediction_record.result, "pending", "display fallback must not rewrite the ledger");
});

test("an official selection result resolves a stale confirming game without a live match", () => {
  const g = { ...game(), options: [{ selection_id: "saved-pick", 적중: false }] };
  assert.equal(recommendationOutcome(g).state, "miss");
  assert.equal(gamePhase(g, { status: "STARTED", finished: false }), "finished");
  g.options[0].selection_id = "different-pick";
  assert.equal(recommendationOutcome(g).state, "pending");
});

test("official round result resolves only the saved line and exact game", () => {
  const g = game("언더오버", "언더", "U 8.5");
  const row = { home: g.home, away: g.away, date: g.date,
    market: "언더오버", label: "U 8.5", n_way: 2, result: "오버" };
  g._officialMarkets = { 1: row };
  assert.equal(recommendationOutcome(g).state, "miss");
  assert.equal(gamePhase(g), "finished");
  row.label = "U 9.5";
  assert.equal(recommendationOutcome(g).state, "pending");
  row.label = "U 8.5";
  row.date = "09.05(토) 18:00";
  assert.equal(recommendationOutcome(g).state, "pending");
});

test("ledger settlement wins over a conflicting live score", () => {
  const g = game();
  g.prediction_record.result = "void";
  assert.equal(recommendationOutcome(g, finalScore(5, 2)).state, "void");
});

test("no score result before explicit finish or when either score is missing", () => {
  assert.equal(recommendationOutcome(game(), { ...finalScore(5, 2), finished: false }).state, "pending");
  for (const invalid of [null, undefined, "", " ", "x", -1, 1.5, true, false]) {
    assert.equal(recommendationOutcome(game(), finalScore(invalid, 2)).state, "pending");
    assert.equal(recommendationOutcome(game(), finalScore(2, invalid)).state, "pending");
  }
  assert.equal(recommendationOutcome(game(), finalScore("0", "1")).state, "miss");
  assert.equal(recommendationOutcome(game(), finalScore(2, 2)).state, "pending");
});

test("no fabricated pick when only postgame recommendation or options exist", () => {
  const g = { ...game(), prediction_record: null, 추천: { 선택: "홈" } };
  assert.equal(recommendationOutcome(g, finalScore(5, 2)).state, "unrecorded");
});

test("totals use the saved line even when the displayed line changed", () => {
  const g = game("언더오버", "언더", "U 8.5");
  g.options = [{ market: "언더오버", label: "U 9.5", 선택: "언더" }];
  assert.equal(recommendationOutcome(g, finalScore(5, 4)).state, "miss");
  assert.equal(recommendationOutcome(game("언더오버", "오버", "U 8.5"), finalScore(5, 4)).state, "hit");
  assert.equal(recommendationOutcome(game("언더오버", "언더", "U 9"), finalScore(5, 4)).state, "void");
  assert.equal(recommendationOutcome(game("언더오버", "언더"), finalScore(5, 4)).state, "pending");
});

test("handicap draw is a winning third selection, not a push on three-way markets", () => {
  const g = game("핸디캡", "핸디홈", "H -1");
  g.options = [{ selection_id: "saved-pick", n_way: 3 }];
  assert.equal(recommendationOutcome(g, finalScore(2, 1)).state, "miss");
  g.prediction_record.selection = "핸디무";
  assert.equal(recommendationOutcome(g, finalScore(2, 1)).state, "hit");
  g.prediction_record.selection = "핸디홈";
  g.options[0].n_way = 2;
  assert.equal(recommendationOutcome(g, finalScore(2, 1)).state, "void");
  assert.equal(recommendationOutcome(game("핸디캡", "핸디원정", "H +1.5"), finalScore(2, 4)).state, "hit");
});

test("unsupported periods and score units never use full-time totals", () => {
  assert.equal(recommendationOutcome(game("전반승패", "전반홈"), finalScore(5, 2)).state, "pending");
  assert.equal(recommendationOutcome({ ...game("언더오버", "언더", "U 180.5"), sport: "vl" }, finalScore(3, 1)).state, "pending");
  const soccer = { ...game("승무패", "무"), sport: "sc" };
  assert.equal(recommendationOutcome(soccer, finalScore(3, 2)).state, "pending");
  assert.equal(recommendationOutcome(soccer, { ...finalScore(3, 2), regular_time_score: [1, 1] }).state, "hit");
});

test("postponed and cancelled feeds wait for official void settlement", () => {
  assert.equal(recommendationOutcome(game(), { ...finalScore(5, 2), cancelled: true }).state, "pending");
  assert.equal(recommendationOutcome(game(), { ...finalScore(5, 2), postponed: true }).state, "pending");
});
