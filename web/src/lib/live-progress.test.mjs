import test from "node:test";
import assert from "node:assert/strict";
import { liveMatchProgress } from "./live-progress.js";

const now = Date.parse("2026-09-05T08:00:00Z");
const live = { status: "STARTED", observed_at: new Date(now).toISOString() };
const progress = (sport, extra = {}, game = {}) => liveMatchProgress({ sport, ...game }, { ...live, ...extra }, now);

test("baseball progress uses completed half innings, independent of picks and scores", () => {
  for (const [status_text, percent] of [["1회초", 0], ["1회말", 5], ["6회초", 55],
    ["6회말", 61], ["9회초", 88], ["9회말", 94]]) {
    assert.equal(progress("bs", { status_text }).percent, percent);
  }
  assert.equal(progress("bs", { inning: 6, batting_side: "away" }).percent, null);
  const mixed = progress("bs", { status_text: "7회", inning: 6, batting_side: "home",
    situation_observed_at: "2026-09-05T07:00:00Z" });
  assert.equal(mixed.percent, null);
  assert.equal(mixed.label, "7회");
  assert.equal(progress("bs", { status_text: "6회초", inning: 8, batting_side: "home", outs: 2 }).percent, 55);
  for (const status_text of ["진행 중", "6회", "0회초"]) assert.equal(progress("bs", { status_text }).percent, null);
  assert.match(progress("bs", { status_text: "10회초" }).label, /연장/);
  assert.equal(progress("bs", { status_text: "10회초" }).percent, null);
});

test("soccer clock is cumulative; unknown, halftime and extra time stay distinct", () => {
  for (const [minute, percent] of [[0, 0], [45, 50], [83, 92], [90, 99], [95, 99]]) {
    assert.equal(progress("sc", { clock: { elapsed_minute: minute } }).percent, percent);
  }
  for (const minute of [null, undefined, "", " ", true, -1, NaN, Infinity]) {
    assert.equal(progress("sc", { clock: { elapsed_minute: minute } }).percent, null);
  }
  assert.equal(progress("sc", { status_text: "하프타임" }).percent, 50);
  assert.equal(progress("sc", { clock: { period: 3, elapsed_minute: 100 }, status_text: "연장 전반" }).percent, null);
});

test("quarters provide a coarse stage only; variable-length sets and unknown phases never imply 50%", () => {
  assert.equal(progress("bk", { status_text: "1쿼터" }).percent, 0);
  assert.equal(progress("bk", { status_text: "3Q 05:00" }).percent, 50);
  assert.equal(progress("bk", { status_text: "연장" }).percent, null);
  assert.equal(progress("vl", { status_text: "3세트" }).percent, null);
  assert.equal(progress("bk", { status_text: "진행 중" }).percent, null);
});

test("finished and disrupted matches override retained clocks and innings", () => {
  for (const status of ["RESULT", "END", "ENDED", "FINAL", "FINISHED"]) {
    assert.equal(progress("bs", { status, status_text: "8회초" }).percent, 100);
  }
  assert.equal(progress("sc", { finished: true, clock: { elapsed_minute: 83 } }).percent, 100);
  assert.equal(progress("bs", { status: "BEFORE", status_text: "1회초" }), null);
  assert.equal(progress("bs", { status: "UNKNOWN" }), null);
  for (const extra of [{ cancelled: true }, { postponed: true }, { status_text: "우천 중단" }]) {
    assert.equal(progress("bs", { status_text: "6회초", ...extra }).state, "interrupted");
    assert.equal(progress("bs", { status_text: "6회초", ...extra }).percent, null);
  }
});

test("freshness uses each row, not a newly published document or wall-clock since kickoff", () => {
  const status_text = "6회초";
  const at = (age) => new Date(now - age).toISOString();
  assert.equal(progress("bs", { status_text, observed_at: at(600000) }).percent, 55);
  assert.equal(progress("bs", { status_text, observed_at: at(600001) }, { _liveFeedAt: at(0) }).state, "stale");
  assert.equal(progress("bs", { status_text, stale: true }).percent, null);
  assert.equal(progress("bs", { status_text, observed_at: null }).percent, null);
  assert.equal(progress("bs", { status_text, observed_at: "invalid" }).percent, null);
  assert.equal(progress("bs", { status_text, observed_at: at(-5001) }).percent, null);
  assert.equal(progress("bs", { status_text, observed_at: null }, { _liveFeedAt: at(0) }).percent, 55);
  assert.equal(progress("bs", { status_text: "우천 중단", observed_at: at(600001) }).state, "stale");
  assert.equal(progress("bs", { status_text: "우천 중단", observed_at: "invalid" }).state, "unknown");
  assert.equal(progress("bs", { status_text: "우천 중단", stale: true }).state, "stale");
});
