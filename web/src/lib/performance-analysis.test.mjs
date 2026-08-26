import test from "node:test";
import assert from "node:assert/strict";
import { performanceAnalysis, predictionFor, signalSummaryFor } from "./performance-analysis.js";

test("확정 추천을 승리 선택으로 쓴다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .54 },
    { market: "승무패", 선택: "무", 모델확률: .25 },
    { market: "승무패", 선택: "패", 모델확률: .21 },
  ] };
  assert.equal(predictionFor(game, game.options[0]).headline, "서울 승리 선택");
});

test("원정 승 표기를 원정팀 승리 선택으로 읽는다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "원정 승", 모델확률: .58 },
    { market: "승무패", 선택: "홈 승", 모델확률: .42 },
  ] };
  assert.equal(predictionFor(game, game.options[0]).headline, "부산 승리 선택");
});

test("추천이 없으면 모델 최고값을 임의 선택하지 않는다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .72 },
  ] };
  assert.equal(predictionFor(game).headline, "경기 모델 추천 제외");
  assert.equal(predictionFor(game).outcome, null);
});

test("모델확률 자체가 없으면 미계산으로 확정한다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "전반언더오버", 선택: "전반언더", 모델확률: null },
  ] };
  assert.equal(predictionFor(game).headline, "경기 모델 미계산");
  assert.equal(predictionFor(game).modelAvailable, false);
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
  for (const label of ["최근 분위기", "공격·수비 균형", "홈·원정 조건", "시즌 위치", "판정 시나리오"]) {
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
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.signalSummary.state, "엇갈림");
  assert.equal(analysis.prediction.headline, "SSG 승리 선택");
  assert.ok(analysis.signalSummary.signals.some((signal) => signal.label === "홈·원정" && signal.side === "한화"));
  assert.equal(analysis.signalSummary.narrative, "최종 선택은 SSG 승리다. 최근 성적에서는 SSG가 앞서고, 홈·원정 성적에서는 한화가 낫다.");
});
test("언더오버 추천에는 팀 우세 합의를 만들지 않는다", () => {
  const game = { home: "SSG", away: "한화", options: [{ market: "언더오버", 선택: "언더", 모델확률: .58, label: "10.5" }] };
  assert.equal(signalSummaryFor(game, predictionFor(game, game.options[0])), null);
});
test("축구 언더오버 근거는 점이 아니라 골로 쓴다", () => {
  const game = { home: "서울", away: "부산", sport: "sc",
    form_home: { avg_scored: 1.2 }, form_away: { avg_scored: 1.4 },
    options: [{ market: "언더오버", line: 2.5, 선택: "오버", 모델확률: .54 }] };
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.ok(analysis.reasons.some((line) => line.includes("득점 합은 2.6골이다")));
  assert.ok(analysis.reasons.every((line) => !line.includes("2.6점")));
});
test("전반 승무패 선택은 전체 경기 승리로 바꾸지 않는다", () => {
  const game = { home: "서울", away: "부산", sport: "sc", options: [
    { market: "전반승무패", 선택: "전반원정", 모델확률: .44, 시장확률: .43 },
  ] };
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.prediction.headline, "부산 전반 승리 선택");
  assert.ok(analysis.reasons.some((line) => line.includes("최종 선택은 부산 전반 승이다.")));
});
test("핸디캡 선택을 실제 경기 승리로 바꾸지 않는다", () => {
  const game = { home: "SSG", away: "한화", sport: "bs", options: [
    { market: "핸디캡", label: "H -2.0", line: -2, 선택: "핸디원정", 모델확률: .76,
      시장확률: .75 },
  ] };
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.prediction.headline, "홈팀 SSG -2.0 적용 후 한화 쪽 선택");
  assert.ok(analysis.reasons.some((line) =>
    line.includes("최종 선택은 홈팀 SSG -2.0 적용 후 한화 쪽이다.")));
  assert.ok(analysis.reasons.every((line) => !line.includes("최종 선택은 한화 승리다")));
});
test("승일패 선택은 실제 점수 차를 유지한다", () => {
  const game = { home: "SSG", away: "한화", sport: "bs", options: [
    { market: "승①패", label: "승①패", 선택: "원정2+", 모델확률: .47,
      시장확률: .46 },
  ] };
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.prediction.headline, "한화 2점 차 이상 승리 선택");
  assert.ok(analysis.reasons.some((line) =>
    line.includes("최종 선택은 한화 2점 차 이상 승이다.")));
});
test("승오패 선택은 내부 코드 대신 실제 점수 차를 쓴다", () => {
  const game = { home: "SK", away: "LG", sport: "bk", options: [
    { market: "승⑤패", label: "승⑤패", 선택: "홈6+", 모델확률: .48, 시장확률: .47 },
    { market: "승⑤패", label: "승⑤패", 선택: "5점차이내", 모델확률: .31, 시장확률: .30 },
  ] };
  assert.equal(predictionFor(game, game.options[0]).headline, "SK 6점 차 이상 승리 선택");
  const middle = predictionFor(game, game.options[1]);
  assert.equal(middle.headline, "5점 차 이내 승부 선택");
  assert.equal(middle.side, null, "5점 차 이내 밴드는 특정 팀 우세 선택이 아니다");
  const reasons = performanceAnalysis(game, game.options[1]).reasons;
  assert.ok(reasons.some((line) =>
    line.includes("최종 선택은 5점 차 이내 승부다.")));
  assert.ok(reasons.every((line) => !line.includes("기준 시나리오는 SK가")));
  assert.ok(reasons.some((line) => line.includes("점수 차 확률과 시장확률로 결정했다")));
});
test("예고 선발만으로 확정 라인업 경고를 숨기지 않는다", () => {
  const game = { home: "SSG", away: "한화", sport: "bs", 선발: {
    home: "김선발", home_detail: { name: "김선발" },
    source_url: "https://www.mlb.com/probable-pitchers", lineup_status: {},
  } };
  assert.equal(performanceAnalysis(game).cautions.length, 1);

  const official = { ...game, 선발: {
    ...game.선발, lineup_status: { state: "official_today", official_today: true },
  } };
  assert.equal(performanceAnalysis(official).cautions.length, 0);
});
test("경기 카드 추천을 분석 제목에도 그대로 사용한다", () => {
  const game = { home: "SSG", away: "한화", options: [
    { market: "승패", 선택: "승", 모델확률: .60 },
    { market: "승패", 선택: "패", 모델확률: .40 },
  ] };
  const recommended = game.options[1];
  assert.equal(predictionFor(game, recommended).headline, "한화 승리 선택");
});
test("팀 이름에 맞는 조사를 사용해 자연어 요약을 만든다", () => {
  const game = { home: "두산", away: "한화",
    form_home: { last10: "7승 3패" },
    form_away: { last10: "3승 7패" },
    options: [{ market: "승패", 선택: "승", 모델확률: .57 }] };
  assert.match(performanceAnalysis(game, game.options[0]).signalSummary.narrative, /두산이 앞선다/);
});
