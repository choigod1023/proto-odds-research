import test from "node:test";
import assert from "node:assert/strict";
import { currentSlipGames, isCurrentSlipDate, recommendedTodayPicks, slipRows } from "./slip.js";

const NOW = new Date("2026-08-30T03:00:00Z");

test("groups selections by Proto game number", () => {
  const games = [{ round: 102, date: "08.30(일) 19:00", home: "홈", away: "원정", options: [
    { market: "승패", "게임번호": "17", "선택": "홈", "배당": 1.7 },
    { market: "승패", "게임번호": "17", "선택": "원정", "배당": 2.1 },
  ] }];
  const rows = slipRows(games, null, NOW);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].number, "17");
  assert.deepEqual(rows[0].selections.map((row) => row.value), [1.7, 2.1]);
});

test("keeps only rounds currently published by the live odds feed", () => {
  const game = (round, number) => ({ round, date: "08.30(일) 19:00", home: "홈", away: "원정", options: [
    { market: "승패", "게임번호": number, "선택": "홈", "배당": 1.7 },
  ] });
  assert.deepEqual(slipRows(
    [game(101, "1"), game(102, "2")], { rounds: [102] }, NOW,
  ).map((row) => row.number), ["2"]);
});

test("hides expired games from the paper slip table", () => {
  const now = new Date("2026-08-30T03:00:00Z");
  assert.equal(isCurrentSlipDate("08.29(토) 19:00", now), false);
  assert.equal(isCurrentSlipDate("08.30(일) 19:00", now), true);
  assert.equal(isCurrentSlipDate("08.31(월) 19:00", now), true);
});

test("marks the generated Proto recommendation on its exact slip selection", () => {
  const home = { market: "승패", label: "", selection_id: "home", "게임번호": "17", "선택": "홈", "배당": 1.7 };
  const away = { market: "승패", label: "", selection_id: "away", "게임번호": "17", "선택": "원정", "배당": 2.1 };
  const games = [{ round: 102, date: "08.30(일) 19:00", home: "홈", away: "원정",
    options: [home, away], "추천": home }];
  const selections = slipRows(games, null, NOW)[0].selections;
  assert.equal(selections.find((row) => row.name === "홈").recommended, true);
  assert.equal(selections.find((row) => row.name === "원정").recommended, false);
});

test("highlights every individual daily pick without using a target combination", () => {
  const home = { market: "승패", label: "", "게임번호": "17", "선택": "홈", "배당": 1.7 };
  const away = { market: "승패", label: "", "게임번호": "17", "선택": "원정", "배당": 2.1 };
  const games = [{ round: 102, date: "08.30(일) 19:00", home: "홈", away: "원정",
    options: [home, away], "추천": away }];
  const today = {
    candidates: [{ round: 102, game_no: "17", market: "승패", sel: "홈",
      odds: 1.7, market_prob: 0.56, predicted_hit_prob: 0.56 }],
  };
  const picks = recommendedTodayPicks(today);
  const selections = slipRows(games, null, NOW, picks)[0].selections;
  assert.equal(selections.find((row) => row.name === "홈").recommended, true);
  assert.equal(selections.find((row) => row.name === "원정").recommended, false,
    "경기별 최종 추천과 다른 선택은 형광 표시하지 않는다");
});

test("combo pass does not erase qualified individual daily picks", () => {
  assert.deepEqual(recommendedTodayPicks({
    recommendation: { action: "pass", recommended_target: 3 },
    candidates: [{ round: 102, game_no: "17", market: "승패", sel: "홈",
      odds: 1.7, market_prob: 0.56, predicted_hit_prob: 0.56 }],
  }).map((row) => row.game_no), ["17"]);
});

test("reprice and align the entire pool before ranking slip highlights", () => {
  const games = ["1", "2", "3", "4"].map((id, i) => {
    const probability = .60 - i * .01;
    const home = { market: "승패", label: "", "게임번호": id, "선택": "홈",
      "배당": 1.7, "시장확률": probability, selection_id: `sel-${id}`, offer_id: `offer-${id}` };
    const away = { ...home, "선택": "원정", "배당": 2.1, "시장확률": 1 - probability,
      selection_id: `away-${id}`, offer_id: `away-offer-${id}` };
    return { event_id: `event-${id}`, round: 102, date: "08.30(일) 19:00",
      home: `홈-${id}`, away: `원정-${id}`, league: "KBO", options: [home, away],
      decision_snapshot: {
        schema_version: "decision-snapshot-v2", event_id: `event-${id}`,
        input_revision_hash: "a".repeat(64), action: "market_reference",
        selection_id: home.selection_id, offer_id: home.offer_id,
        as_of: "2026-08-30T09:00:00+09:00",
        probability: { market: probability, final: probability, ai_delta_applied: 0 },
        model: { status: "shadow", validated_edge: false, promotion_gate: "not_passed" },
        stages: { market: { status: "used" }, structured_ai: { status: "shadow" },
          availability_ai: { status: "missing" }, language_ai: { status: "template" } },
        evidence: [], audit: { feature_cutoff_at: "2026-08-30T09:00:00+09:00",
          built_at: "2026-08-30T09:00:01+09:00" },
      } };
  });
  const today = { year: 2026, candidates: games.map((game) => ({
    event_key: game.event_id, date: game.date, home: game.home, away: game.away,
    league: game.league, round: 102, game_no: game.options[0]["게임번호"],
    market: "승패", sel: "홈", odds: 1.7, market_prob: game.options[0]["시장확률"],
  })) };
  const before = recommendedTodayPicks(today, currentSlipGames(games, null, NOW), NOW.getTime());
  assert.deepEqual(before.map((row) => row.game_no), ["1", "2", "3"]);
  // today payload is unchanged. Only the live price vector changed.
  const liveOdds = { rounds: [102], generated_at: "2026-08-30T12:01:00+09:00",
    odds: { "102": { "1": [1.2, 3.5] } } };
  const current = currentSlipGames(games, liveOdds, NOW);
  assert.equal(current[0]._liveOddsChanged, true);
  const after = recommendedTodayPicks(today, current, NOW.getTime());
  assert.deepEqual(after.map((row) => row.game_no), ["2", "3", "4"],
    "stale pick is withheld and previously fourth eligible pick fills its slot");
  const rows = slipRows(current, null, NOW, after);
  assert.equal(rows.find((row) => row.number === "1").selections[0].value, 1.2);
  assert.equal(rows.find((row) => row.number === "1").selections[0].recommended, false);
  assert.equal(rows.find((row) => row.number === "4").selections[0].recommended, true);
});

