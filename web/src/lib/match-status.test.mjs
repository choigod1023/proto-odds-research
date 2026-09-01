import assert from "node:assert/strict";
import test from "node:test";
import { gamePhase, recommendationOutcome } from "./match-status.js";

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

test("실시간 매칭이 없어도 시작 후 8시간이 지난 경기는 예정으로 남기지 않는다", () => {
  const now = new Date("2026-08-30T12:01:00+09:00").getTime();
  assert.equal(gamePhase({ year: 2026, date: "08.30(일) 03:00", status: "경기전" }, null, now), "pending");
  assert.equal(gamePhase({ year: 2026, date: "08.30(일) 10:00", status: "경기전" }, null, now), "upcoming");
});

test("사전 저장 추천만 적중과 실패로 표시한다", () => {
  assert.equal(recommendationOutcome({ prediction_record: { result: "hit" } }).label, "적중");
  assert.equal(recommendationOutcome({ prediction_record: { result: "miss" } }).label, "적중 실패");
  assert.equal(recommendationOutcome({}).state, "unrecorded");
  assert.equal(recommendationOutcome({}).label, "사전 기록 전 경기");
});
