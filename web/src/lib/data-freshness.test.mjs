import test from "node:test";
import assert from "node:assert/strict";
import { freshnessStatus, isDataStale, latestGeneratedAt, waitingLabel } from "./data-freshness.js";

const now = Date.parse("2026-08-24T10:00:00+09:00");

test("3시간 이상 지난 데이터는 지연으로 판정한다", () => {
  assert.equal(isDataStale("2026-08-24T07:00:00+09:00", now), true);
  assert.equal(isDataStale("2026-08-24T09:00:00+09:00", now), false);
});

test("오래된 배당 대기는 데이터 갱신 지연으로 표시한다", () => {
  assert.equal(waitingLabel({ date: "08.24(월) 12:00" }, {
    generatedAt: "2026-08-23T11:38:00+09:00", year: 2026, now,
  }), "데이터 갱신 지연");
});

test("최신 데이터라도 시작 시각이 지났으면 배당 발표 전이라고 하지 않는다", () => {
  assert.equal(waitingLabel({ date: "08.24(월) 08:00" }, {
    generatedAt: "2026-08-24T09:30:00+09:00", year: 2026, now,
  }), "상태 확인 불가");
});

test("배당 파일보다 경기 원장이 최신이면 최신 원장 시각을 사용한다", () => {
  assert.equal(latestGeneratedAt(
    "2026-08-24T06:00:00+09:00",
    "2026-08-24T09:30:00+09:00",
  ), "2026-08-24T09:30:00+09:00");
  assert.equal(isDataStale(latestGeneratedAt(
    "2026-08-24T06:00:00+09:00",
    "2026-08-24T09:30:00+09:00",
  ), now), false);
});

test("유효하지 않은 시각은 최신값 선택에서 제외한다", () => {
  assert.equal(latestGeneratedAt(null, "", "2026-08-24T09:00:00+09:00"),
    "2026-08-24T09:00:00+09:00");
});

test("실시간 소스 첫 확인 전에는 오래된 정적 파일로 경고를 확정하지 않는다", () => {
  assert.equal(freshnessStatus({
    staticGeneratedAt: "2026-08-24T06:00:00+09:00",
    liveGeneratedAt: null,
    liveChecked: false,
    now,
  }), "checking");
});

test("실시간 확인 후에는 최신 응답으로 정적 원장의 지연을 해소한다", () => {
  assert.equal(freshnessStatus({
    staticGeneratedAt: "2026-08-24T06:00:00+09:00",
    liveGeneratedAt: "2026-08-24T09:55:00+09:00",
    liveChecked: true,
    now,
  }), "fresh");
});

test("실시간 확인까지 실패하고 정적 원장도 오래됐을 때만 지연을 확정한다", () => {
  assert.equal(freshnessStatus({
    staticGeneratedAt: "2026-08-24T06:00:00+09:00",
    liveGeneratedAt: null,
    liveChecked: true,
    now,
  }), "stale");
});
