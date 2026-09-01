import test from "node:test";
import assert from "node:assert/strict";
import { isCurrentSlipDate, recommendedTodayPicks, slipRows } from "./slip.js";

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
    candidates: [{ round: 102, game_no: "17", market: "승패", sel: "홈" }],
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
    candidates: [{ round: 102, game_no: "17" }],
  }), [{ round: 102, game_no: "17" }]);
});

