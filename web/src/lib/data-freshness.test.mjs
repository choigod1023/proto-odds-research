import test from "node:test";
import assert from "node:assert/strict";
import { isDataStale, waitingLabel } from "./data-freshness.js";

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