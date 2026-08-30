import test from "node:test";
import assert from "node:assert/strict";
import { kstMMDD, nextKstDateRefreshDelay } from "./fmt.js";

test("KST 자정이 지나면 전날의 내일 경기가 오늘 날짜로 재분류된다", () => {
  const before = Date.parse("2026-08-30T23:59:50+09:00");
  const after = Date.parse("2026-08-31T00:00:01+09:00");

  assert.equal(kstMMDD(1, before), "08.31");
  assert.equal(kstMMDD(0, after), "08.31");
  assert.equal(nextKstDateRefreshDelay(before), 11_000);
});

test("UTC 날짜가 달라도 KST 날짜를 기준으로 계산한다", () => {
  const lateKst = Date.parse("2026-08-31T00:30:00+09:00");

  assert.equal(kstMMDD(0, lateKst), "08.31");
  assert.equal(kstMMDD(1, lateKst), "09.01");
});
