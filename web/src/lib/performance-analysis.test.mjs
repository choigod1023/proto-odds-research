import test from "node:test";
import assert from "node:assert/strict";
import { performanceAnalysis, predictionFor, signalSummaryFor } from "./performance-analysis.js";

test("가장 높은 승무패 모델 확률을 예상으로 쓴다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .54 },
    { market: "승무패", 선택: "무", 모델확률: .25 },
    { market: "승무패", 선택: "패", 모델확률: .21 },
  ] };
  assert.equal(predictionFor(game).headline, "서울 우세");
});

test("원정 승 표기를 원정팀 우세로 읽는다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "원정 승", 모델확률: .58 },
    { market: "승무패", 선택: "홈 승", 모델확률: .42 },
  ] };
  assert.equal(predictionFor(game).headline, "부산 우세");
});

test("최근 흐름을 경기력 문장으로 설명한다", () => {
  const game = { home: "서울", away: "부산", sport: "sc",
    form_home: { streak: "3연승", trend: "상승", avg_scored: 2.1, avg_conceded: .8 },
    form_away: { trend: "하락", avg_scored: 1, avg_conceded: 1.9 },
    options: [{ market: "승무패", 선택: "승", 모델확률: .55 }] };
  assert.ok(performanceAnalysis(game).reasons.some((line) => line.includes("3연승 흐름")));
});

test("충분한 자료가 있으면 경기 관점별 해설을 제공한다", () => {
  const game = { home: "서울", away: "부산", sport: "sc",
    form_home: { last10: "7승 2무 1패", streak: "3연승", trend: "상승", home: "6-1", avg_scored: 2.1, avg_conceded: .8 },
    form_away: { last10: "4승 2무 4패", trend: "하락", away: "2-4", avg_scored: 1.2, avg_conceded: 1.7 },
    options: [{ market: "승무패", 선택: "승", 모델확률: .55 }],
    선발: { teams: {
      home: { rank: 2, wins: 12, draws: 4, losses: 3, points: 40 },
      away: { rank: 8, wins: 7, draws: 3, losses: 9, points: null },
    } } };
  const reasons = performanceAnalysis(game).reasons;
  assert.ok(reasons.length >= 5);
  for (const label of ["최근 분위기", "공격·수비 균형", "홈·원정 조건", "시즌 위치", "예상 경기 흐름"]) {
    assert.ok(reasons.some((line) => line.startsWith(`${label} — `)), label);
  }
  assert.ok(reasons.every((line) => !line.includes("null점")));
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

test("모델 추천과 경기력 지표가 반대면 엇갈림을 숨기지 않는다", () => {
  const game = { home: "SSG", away: "한화", sport: "bs",
    form_home: { last10: "4승 6패", home: "12-16", avg_scored: 5.5, avg_conceded: 6.6 },
    form_away: { last10: "2승 8패", away: "16-17", avg_scored: 3.1, avg_conceded: 4.3 },
    options: [{ market: "승패", 선택: "승", 모델확률: .52 }] };
  const analysis = performanceAnalysis(game);
  assert.equal(analysis.signalSummary.state, "엇갈림");
  assert.match(analysis.prediction.headline, /경기력 신호 엇갈림/);
  assert.ok(analysis.signalSummary.signals.some((signal) => signal.label === "홈·원정" && signal.side === "한화"));
});
test("언더오버 추천에는 팀 우세 합의를 만들지 않는다", () => {
  const game = { home: "SSG", away: "한화", options: [{ market: "언더오버", 선택: "언더", 모델확률: .58, label: "10.5" }] };
  assert.equal(signalSummaryFor(game, predictionFor(game)), null);
});
test("경기 카드 추천을 분석 제목에도 그대로 사용한다", () => {
  const game = { home: "SSG", away: "한화", options: [
    { market: "승패", 선택: "승", 모델확률: .60 },
    { market: "승패", 선택: "패", 모델확률: .40 },
  ] };
  const recommended = game.options[1];
  assert.equal(predictionFor(game, recommended).headline, "한화 우세");
});