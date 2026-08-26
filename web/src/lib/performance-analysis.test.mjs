import test from "node:test";
import assert from "node:assert/strict";
import { performanceAnalysis, predictionFor, signalSummaryFor } from "./performance-analysis.js";

let decisionSequence = 0;
function withDecision(input, selectedIndex = 0) {
  decisionSequence += 1;
  const eventId = `evt_performance_${decisionSequence}`;
  const options = (input.options || []).map((option, index) => ({
    ...option,
    selection_id: `sel_${decisionSequence}_${index}`,
    offer_id: `off_${decisionSequence}_${index}`,
  }));
  const selected = options[selectedIndex];
  return {
    ...input,
    event_id: eventId,
    options,
    decision_snapshot: {
      schema_version: "decision-snapshot-v2",
      event_id: eventId,
      input_revision_hash: String(decisionSequence).padStart(64, "a").slice(-64),
      action: "market_reference",
      selection_id: selected.selection_id,
      offer_id: selected.offer_id,
      as_of: "2026-08-27T09:00:00+09:00",
      audit: {
        feature_cutoff_at: "2026-08-27T09:00:00+09:00",
        built_at: "2026-08-27T09:00:01+09:00",
        pre_registered: false,
      },
      probability: {
        market: selected.시장확률,
        ai_candidate: selected.모델확률,
        ai_delta_applied: 0,
        final: selected.시장확률,
      },
      model: {
        status: "shadow", validated_edge: false, promotion_gate: "not_passed",
        operating_version: "shin-market-anchor-v1", artifact_hash: null,
      },
      stages: {
        market: { status: "used", affects_probability: true },
        structured_ai: { status: "shadow", affects_probability: false },
        availability_ai: { status: "missing", affects_probability: false },
        language_ai: { status: "template", affects_probability: false },
      },
      evidence: [],
    },
  };
}

test("추천이 없으면 가장 높은 모델 확률을 예상으로 만들지 않는다", () => {
  const game = { home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .94, 시장확률: .54 },
    { market: "승무패", 선택: "무", 모델확률: .03, 시장확률: .25 },
    { market: "승무패", 선택: "패", 모델확률: .03, 시장확률: .21 },
  ] };
  assert.equal(predictionFor(game).headline, "판정 계약 오류 · 보류");
  assert.equal(predictionFor(game).probability, null);
});

test("생성기가 고른 시장 기준 선택만 경기 예상으로 읽는다", () => {
  const game = withDecision({ home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "승", 모델확률: .33, 시장확률: .54 },
    { market: "승무패", 선택: "무", 모델확률: .46, 시장확률: .25 },
    { market: "승무패", 선택: "패", 모델확률: .21, 시장확률: .21 },
  ] });
  const prediction = predictionFor(game, game.options[0]);
  assert.equal(prediction.headline, "시장 기준 · 서울 우세");
  assert.equal(prediction.probability, .54);
});

test("원정 승 표기를 원정팀 우세로 읽는다", () => {
  const game = withDecision({ home: "서울", away: "부산", options: [
    { market: "승무패", 선택: "원정 승", 모델확률: .58, 시장확률: .52 },
    { market: "승무패", 선택: "홈 승", 모델확률: .42, 시장확률: .48 },
  ] });
  assert.equal(predictionFor(game, game.options[0]).headline, "시장 기준 · 부산 우세");
});

test("최근 흐름을 경기력 문장으로 설명한다", () => {
  const game = withDecision({ home: "서울", away: "부산", sport: "sc",
    form_home: { streak: "3연승", trend: "상승", avg_scored: 2.1, avg_conceded: .8 },
    form_away: { trend: "하락", avg_scored: 1, avg_conceded: 1.9 },
    options: [{ market: "승무패", 선택: "승", 모델확률: .55, 시장확률: .53 }] });
  assert.ok(performanceAnalysis(game, game.options[0]).reasons.some((line) => line.includes("3연승 흐름")));
});

test("충분한 자료가 있으면 경기 관점별 해설을 제공한다", () => {
  const game = withDecision({ home: "서울", away: "부산", sport: "sc",
    form_home: { last10: "7승 2무 1패", streak: "3연승", trend: "상승", home: "6-1", avg_scored: 2.1, avg_conceded: .8 },
    form_away: { last10: "4승 2무 4패", trend: "하락", away: "2-4", avg_scored: 1.2, avg_conceded: 1.7 },
    options: [{ market: "승무패", 선택: "승", 모델확률: .55, 시장확률: .53 }],
    선발: { teams: {
      home: { rank: 2, wins: 12, draws: 4, losses: 3, points: 40 },
      away: { rank: 8, wins: 7, draws: 3, losses: 9, points: null },
    } } });
  const reasons = performanceAnalysis(game, game.options[0]).reasons;
  assert.ok(reasons.length >= 5);
  for (const label of ["최근 분위기", "공격·수비 균형", "홈·원정 조건", "시즌 위치", "예상 경기 흐름"]) {
    assert.ok(reasons.some((line) => line.startsWith(`${label} — `)), label);
  }
  assert.ok(reasons.every((line) => !line.includes("null점")));
});

test("축구 요약은 실제 선수와 공격 기록을 보여준다", () => {
  const game = withDecision({ home: "서울", away: "부산", sport: "sc",
    options: [{ market: "승무패", 선택: "승", 모델확률: .55, 시장확률: .53 }],
    선발: { key_players: { home: [{ name: "김공격", goals: 8, assists: 3 }], away: [{ name: "박도움", goals: 2, assists: 7 }] } } });
  const result = performanceAnalysis(game, game.options[0]);
  assert.deepEqual(result.featuredPlayers.map((player) => player.name), ["김공격", "박도움"]);
  assert.match(result.featuredPlayers[0].detail, /8골/);
});

test("야구 요약은 선발투수 맞대결을 포함한다", () => {
  const game = withDecision({ home: "두산", away: "한화", sport: "bs",
    options: [{ market: "승패", 선택: "승", 모델확률: .51, 시장확률: .52 }],
    선발: { home: "김선발", away: "박선발" } });
  const result = performanceAnalysis(game, game.options[0]);
  assert.deepEqual(result.featuredPlayers.map((player) => player.name), ["김선발", "박선발"]);
  assert.match(result.playerNotes[0], /선발 맞대결/);
  assert.match(result.playerNotes[0], /김선발과/);
});

test("모델 추천과 경기력 지표가 반대면 엇갈림을 숨기지 않는다", () => {
  const game = withDecision({ home: "SSG", away: "한화", sport: "bs",
    form_home: { last10: "4승 6패", home: "12-16", avg_scored: 5.5, avg_conceded: 6.6 },
    form_away: { last10: "2승 8패", away: "16-17", avg_scored: 3.1, avg_conceded: 4.3 },
    options: [{ market: "승패", 선택: "승", 모델확률: .52, 시장확률: .54 }] });
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.signalSummary.state, "엇갈림");
  assert.equal(analysis.prediction.headline, "시장 기준 · SSG 우세");
  assert.ok(analysis.signalSummary.signals.some((signal) => signal.label === "홈·원정" && signal.side === "한화"));
  assert.equal(analysis.signalSummary.narrative, "최근 성적에서는 SSG가 앞서고, 홈·원정 성적에서는 한화가 낫다. 엇갈림은 숨기지 않되 최종 값은 시장 기준으로 유지한다.");
});

test("예상 흐름 문장의 목적격 조사를 자연스럽게 붙인다", () => {
  const game = withDecision({ home: "두산", away: "한화", sport: "bs",
    options: [{ market: "승패", 선택: "승", 시장확률: .55, 모델확률: .56 }] });
  const result = performanceAnalysis(game, game.options[0]);
  assert.match(result.reasons.at(-1), /경기 정보를 바탕으로/);
  assert.doesNotMatch(result.reasons.at(-1), /정보을/);
});
test("언더오버 추천에는 팀 우세 합의를 만들지 않는다", () => {
  const game = withDecision({ home: "SSG", away: "한화", options: [{ market: "언더오버", 선택: "언더", 모델확률: .58, 시장확률: .53, label: "10.5" }] });
  assert.equal(signalSummaryFor(game, predictionFor(game, game.options[0])), null);
});
test("핸디캡 적중 방향을 실제 원정팀 승리로 해석하지 않는다", () => {
  const game = withDecision({ home: "디트타이", away: "탬파레이", sport: "bs",
    options: [{ market: "핸디캡", 선택: "핸디원정", label: "H -2.5",
      시장확률: .772, 모델확률: .81 }] });
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.prediction.side, null);
  assert.equal(analysis.prediction.headline, "시장 기준 · H -2.5 핸디원정");
  assert.match(analysis.reasons.at(-1), /실제 승리 예측과는 다르다/);
  assert.doesNotMatch(analysis.reasons.at(-1), /탬파레이가.*주도권/);
});
test("전반 언더오버를 홈팀 우세로 해석하지 않는다", () => {
  const game = withDecision({ home: "AEK아테", away: "L소피아", sport: "sc",
    options: [{ market: "전반언더오버", 선택: "전반언더", label: "h U 1.5",
      시장확률: .70, 모델확률: .72 }] });
  const analysis = performanceAnalysis(game, game.options[0]);
  assert.equal(analysis.prediction.side, null);
  assert.equal(analysis.prediction.headline, "시장 기준 · h U 1.5 전반언더");
  assert.match(analysis.reasons.at(-1), /전반 득점 기준/);
  assert.doesNotMatch(analysis.prediction.headline, /AEK아테 우세/);
});
test("경기 카드 추천을 분석 제목에도 그대로 사용한다", () => {
  const game = withDecision({ home: "SSG", away: "한화", options: [
    { market: "승패", 선택: "승", 모델확률: .60, 시장확률: .40 },
    { market: "승패", 선택: "패", 모델확률: .40, 시장확률: .60 },
  ] }, 1);
  const recommended = game.options[1];
  assert.equal(predictionFor(game, recommended).headline, "시장 기준 · 한화 우세");
});
test("팀 이름에 맞는 조사를 사용해 자연어 요약을 만든다", () => {
  const game = withDecision({ home: "두산", away: "한화",
    form_home: { last10: "7승 3패" },
    form_away: { last10: "3승 7패" },
    options: [{ market: "승패", 선택: "승", 모델확률: .57, 시장확률: .55 }] });
  assert.match(performanceAnalysis(game, game.options[0]).signalSummary.narrative, /두산이 앞선다/);
});
