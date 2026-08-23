import test from "node:test";
import assert from "node:assert/strict";
import { performanceAnalysis, predictionFor } from "./performance-analysis.js";

test("가장 높은 승무패 모델 확률을 예상으로 쓴다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .54 },
    { market: "승무패", 선택: "무", 모델확률: .25 },
    { market: "승무패", 선택: "패", 모델확률: .21 },
  ] };
  assert.equal(predictionFor(game).headline, "서울 우세");
});

test("최근 흐름을 경기력 문장으로 설명한다", () => {
  const game = { home: "서울", away: "부산", sport: "sc",
    form_home: { streak: "3연승", trend: "상승", avg_scored: 2.1, avg_conceded: .8 },
    form_away: { trend: "하락", avg_scored: 1, avg_conceded: 1.9 },
    options: [{ market: "승무패", 선택: "승", 모델확률: .55 }] };
  assert.ok(performanceAnalysis(game).reasons.some((line) => line.includes("3연승 흐름")));
});

test("축구 요약은 실제 선수와 공격 기록을 보여준다", () => {
  const game = { home: "서울", away: "부산", sport: "sc",
    options: [{ market: "승무패", 선택: "승", 모델확률: .55 }],
    선발: { key_players: { home: [{ name: "김공격", goals: 8, assists: 3 }], away: [{ name: "박도움", goals: 2, assists: 7 }] } } };
  const result = performanceAnalysis(game);
  assert.deepEqual(result.featuredPlayers.map((player) => player.name), ["김공격", "박도움"]);
  assert.match(result.featuredPlayers[0].detail, /8골/);
});

test("야구 요약은 선발투수 맞대결을 포함한다", () => {
  const result = performanceAnalysis({ home: "두산", away: "한화", sport: "bs",
    options: [{ market: "승패", 선택: "승", 모델확률: .51 }], 선발: { home: "김선발", away: "박선발" } });
  assert.deepEqual(result.featuredPlayers.map((player) => player.name), ["김선발", "박선발"]);
  assert.match(result.playerNotes[0], /선발 맞대결/);
});
