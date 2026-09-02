import assert from "node:assert/strict";
import test from "node:test";
import { decisionFrozen, gamePhase, recommendationOutcome } from "./match-status.js";

test("최종 픽은 경기 시작 30분 전부터 고정한다", () => {
  const game = { year: 2026, date: "09.02(수) 18:00" };
  assert.equal(decisionFrozen(game, new Date("2026-09-02T17:29:59+09:00").getTime()), false);
  assert.equal(decisionFrozen(game, new Date("2026-09-02T17:30:00+09:00").getTime()), true);
});

test("실시간 피드가 시작 상태면 원본 정산 상태보다 진행 중을 우선한다", () => {
  assert.equal(gamePhase({ status: "결과확인" }, {
    status: "STARTED", finished: false, home_score: 2, away_score: 1,
  }), "live");
});

test("실시간 종료와 프로토 정산을 모두 종료로 분류한다", () => {
  assert.equal(gamePhase({}, { status: "RESULT", finished: true }), "finished");
  assert.equal(gamePhase({ status: "정산" }, null), "finished");
});

test("취소와 연기는 진행 중으로 오인하지 않는다", () => {
  assert.equal(gamePhase({}, { status: "CANCEL", cancelled: true }), "pending");
  assert.equal(gamePhase({}, { status: "POSTPONED", postponed: true }), "pending");
});

test("10분 넘게 갱신되지 않은 시작 상태를 계속 진행 중으로 표시하지 않는다", () => {
  const now = new Date("2026-09-01T20:30:00+09:00").getTime();
  const game = { status: "경기전", _liveFeedAt: "2026-09-01T11:00:00Z" };
  const live = { status: "STARTED", finished: false };
  assert.equal(gamePhase(game, live, now), "pending");
});

test("실시간 매칭이 없으면 시작 15분 뒤 상태 확인 대상으로 전환한다", () => {
  const game = { year: 2026, date: "08.30(일) 10:00", status: "경기전" };
  assert.equal(gamePhase(game, null, new Date("2026-08-30T10:15:00+09:00").getTime()), "upcoming");
  assert.equal(gamePhase(game, null, new Date("2026-08-30T10:16:00+09:00").getTime()), "pending");
});

test("사전 저장 추천만 적중과 실패로 표시한다", () => {
  assert.equal(recommendationOutcome({ prediction_record: { result: "hit" } }).label, "적중");
  assert.equal(recommendationOutcome({ prediction_record: { result: "miss" } }).label, "적중 실패");
  assert.equal(recommendationOutcome({}).state, "unrecorded");
  assert.equal(recommendationOutcome({}).label, "사전 기록 전 경기");
});
