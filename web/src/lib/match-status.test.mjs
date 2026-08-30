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

test("사전 저장 추천만 적중과 실패로 표시한다", () => {
  assert.equal(recommendationOutcome({ prediction_record: { result: "hit" } }).label, "적중");
  assert.equal(recommendationOutcome({ prediction_record: { result: "miss" } }).label, "적중 실패");
  assert.equal(recommendationOutcome({}).state, "unrecorded");
});
