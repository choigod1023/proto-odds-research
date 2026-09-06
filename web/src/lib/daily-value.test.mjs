import test from "node:test";
import assert from "node:assert/strict";
import { compareDailyValue, dailyValueDecisions, dailyValueMetrics } from "./daily-value.js";
import { dailyHighlightedSelections, dailyRecommendationDecisions } from "./unified-recommendation.js";

const candidate = (id, odds, p, extra = {}) => ({
  event_key: id, game_no: id, round: 105, market: "승패", sel: "홈",
  year: 2026, date: "09.05(토) 18:00", league: "KBO", odds, market_prob: p,
  is_market_favorite: true, ...extra,
});
const validated = {
  decision_pipeline_applied: true, has_validated_edge: true,
  predicted_hit_prob: .62, probability_lower_bound: .58,
  probability_interval: [.58, .66], validated_uncertainty_available: true,
  uncertainty_source: "validated_residual_interval",
};

test("better return outranks low price / higher hit; no high-odds bonus", () => {
  const cheap = candidate("cheap", 1.5, .59);
  const value = candidate("value", 1.72, .56);
  const pricey = candidate("pricey", 1.95, .45);
  assert.ok(compareDailyValue(value, cheap) < 0);
  const picks = dailyHighlightedSelections([
    cheap, candidate("a", 1.65, .59), candidate("b", 1.70, .57), value, pricey,
  ]);
  assert.ok(picks.includes(value));
  assert.ok(!picks.includes(cheap));
  assert.ok(!picks.includes(pricey));
  assert.ok(compareDailyValue(candidate("good-cheap", 1.5, .65), value) < 0,
    "low odds still win if their return is actually better");
});

test("50-55% selections participate; weak price is independently rejected", () => {
  assert.equal(dailyValueMetrics(candidate("balanced", 1.8, .52)).qualifies, true);
  assert.equal(dailyValueMetrics(candidate("weak-price", 1.5, .55)).qualifies, false);
  assert.equal(dailyValueMetrics(candidate("boundary", 1.7, .5)).qualifies, true);
  assert.equal(dailyValueMetrics(candidate("below", 1.7, .499999)).qualifies, false);
});

test("fake model probability, lower bound and historical ROI cannot boost fallback", () => {
  const base = candidate("fallback", 1.7, .56);
  assert.deepEqual(dailyValueMetrics({ ...base, predicted_hit_prob: .99,
    probability_lower_bound: .98, probability_interval: [.98, .999],
    hist_roi: 5, hist_n: 999999, policy_authorized: true,
  }), dailyValueMetrics(base));
});

test("full validated interval reduces score; malformed intervals use market comparison", () => {
  const row = candidate("validated", 1.8, .54, validated);
  const metrics = dailyValueMetrics(row);
  assert.equal(metrics.validated_interval, true);
  assert.equal(metrics.comparison_probability, .58);
  assert.equal(metrics.expected_return, .62 * 1.8 - 1);
  assert.equal(metrics.break_even_probability, 1 / 1.8);
  for (const patch of [
    { probability_interval: null }, { probability_interval: [.58] },
    { probability_interval: [.58, .60] }, { probability_interval: [.65, .58] },
    { probability_lower_bound: .57 }, { probability_interval: [.58, null] },
    { uncertainty_source: "made_up" }, { validated_uncertainty_available: false },
  ]) {
    const value = dailyValueMetrics({ ...row, ...patch });
    assert.equal(value.validated_interval, false);
    assert.equal(value.comparison_probability, .54);
  }
});

test("only a verified positive lower return allows more than three per league/day", () => {
  const rows = [.75, .72, .7, .68].map((p, i) => candidate(String(i), 1.6, p));
  assert.equal(dailyHighlightedSelections(rows).length, 3);
  const extra = candidate("extra", 1.8, .54, validated);
  assert.equal(dailyValueDecisions([...rows, extra]).find((r) => r.selection === extra)
    .reason_code, "validated_extra");
  const otherDay = candidate("tomorrow", 1.8, .52, { date: "09.06(일) 02:00" });
  assert.ok(dailyHighlightedSelections([...rows, otherDay]).includes(otherDay));
});

test("same-probability high-hit overflow and ineligible prices remain excluded", () => {
  const rows = ["a", "b", "c", "d"].map((id) => candidate(id, 1.5, .66));
  assert.deepEqual(dailyHighlightedSelections(rows).map((r) => r.game_no), ["a", "b", "c"]);
  for (const patch of [{ odds: 2.2 }, { is_market_favorite: false },
    { final_reversal: true }, { market: "홀짝" }]) {
    assert.equal(dailyHighlightedSelections([candidate("unsafe", 1.7, .6, patch)]).length, 0);
  }
});

test("invalid inputs never become value signals", () => {
  for (const bad of [null, "", false, true, "nonsense", NaN, Infinity, 0, 1]) {
    assert.equal(dailyValueMetrics(candidate("bad", 1.7, bad)).qualifies, false);
    assert.equal(dailyValueMetrics(candidate("bad", bad, .6)).qualifies, false);
  }
  assert.equal(dailyHighlightedSelections([null]).length, 0);
  for (const bad of [[1.7], [.56], {}, [], "0x1", "0_5"]) {
    assert.equal(dailyValueMetrics(candidate("bad", bad, .6)).qualifies, false);
    assert.equal(dailyValueMetrics(candidate("bad", 1.7, bad)).qualifies, false);
  }
});

test("same-day ISO and date-only candidates without years share a quota", () => {
  const rows = ["a", "b", "c", "d"].map((id) => candidate(id, 1.5, .66, { year: undefined }));
  rows[3].kickoff_at = "2026-09-05T09:00:00Z";
  assert.equal(dailyHighlightedSelections(rows).length, 3);
});

test("four-decimal serialized lower retains full-precision validated interval", () => {
  const row = candidate("rounded", 1.8, .54, { ...validated,
    probability_lower_bound: .5833, probability_interval: [.583333, .66] });
  const value = dailyValueMetrics(row);
  assert.equal(value.validated_interval, true);
  assert.equal(value.comparison_probability, .583333);
});

test("explanations expose return and break-even; fallback is not a verified lower bound", () => {
  const row = dailyRecommendationDecisions([candidate("value", 1.8, .52)])[0];
  assert.equal(row.recommended, true);
  assert.match(row.reason, /비교 기대수익 -6.4%/);
  assert.match(row.display.text, /손익분기 55.6%/);
  assert.match(row.display.text, /시장 기준 비교값/);
  assert.doesNotMatch(row.display.text, /검증 하한/);
  assert.match(row.counterReason, /기대손실/);
});
