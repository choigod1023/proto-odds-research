import assert from "node:assert/strict";
import { alignTodayRecommendations, buildTodayMemberships, canonicalOption, canonicalPick,
  dailyHighlightedSelections,
  dailyRecommendationDecisions,
  selectionKey, todaySelectionForGame } from "./unified-recommendation.js";

const home = {
  selection_id: "sel_home", offer_id: "off_home",
  market: "승패", label: "", "게임번호": "10", "선택": "홈",
  "배당": 1.55, "시장확률": .62,
};
const away = {
  selection_id: "sel_away", offer_id: "off_away",
  market: "승패", label: "", "게임번호": "10", "선택": "원정",
  "배당": 2.1, "시장확률": .38,
};
const game = {
  event_id: "evt_game",
  round: 7,
  // 레거시 추천은 반대로 주입해도 snapshot만 따라야 한다.
  추천: away,
  options: [home, away],
  decision_snapshot: {
    schema_version: "decision-snapshot-v2",
    event_id: "evt_game",
    input_revision_hash: "a".repeat(64),
    action: "market_reference",
    selection_id: "sel_home",
    offer_id: "off_home",
    as_of: "2026-08-27T09:00:00+09:00",
    probability: { market: .62, ai_delta_applied: 0, final: .62 },
    model: {
      status: "shadow", validated_edge: false, promotion_gate: "not_passed",
      operating_version: "shin-market-anchor-v1",
    },
    stages: {
      market: { status: "used" },
      structured_ai: { status: "shadow" },
      availability_ai: { status: "missing" },
      language_ai: { status: "template" },
    },
    evidence: [],
    audit: {
      feature_cutoff_at: "2026-08-27T09:00:00+09:00",
      built_at: "2026-08-27T09:00:01+09:00",
    },
  },
};
const grades = { odds_bins: [{ bin: "1.5-1.8", roi: -.1, hit: .6, grade: "B" }] };

assert.equal(canonicalOption(game), home, "v2 snapshot 선택을 현재 선택지에 연결한다");
assert.equal(canonicalPick(game, game.options, grades).o, home);

const today = { candidates: [
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "홈", odds: 1.55 },
  { round: 7, game_no: "10", market: "승패", market_label: "", sel: "원정", odds: 2.1 },
] };
const alignedToday = alignTodayRecommendations(today, [game]);
assert.deepEqual(alignedToday.candidates.map((row) => row.sel), ["홈"]);
assert.equal(alignedToday.candidates[0].recommendation_basis, "game-decision");
assert.equal(alignedToday.candidates[0].predicted_hit_prob, 0.62);
assert.equal(alignedToday.candidates[0].probability_source, "shin_market_fallback");
assert.equal(alignedToday.candidates[0].has_validated_edge, false);
assert.equal(alignedToday.candidates[0].decision_pipeline_applied, false);
assert.deepEqual(alignedToday.alignment, {
  input_candidates: 2,
  safe_candidates: 1,
  game_model_candidates: 1,
  market_fallback_candidates: 0,
  dropped_by_safety: 1,
});

const unmatched = { candidates: [
  { round: 8, game_no: "20", market: "승패", market_label: "", sel: "원정",
    odds: 1.85, is_market_favorite: true },
] };
assert.equal(alignTodayRecommendations(unmatched, [game]).candidates.length, 0,
  "단일 경기 판정과 맞지 않는 이름뿐인 보완 후보는 제거한다");
assert.equal(alignTodayRecommendations(unmatched, []).candidates.length, 0,
  "경기 판정 원장이 없으면 브라우저가 새 방향을 만들지 않는다");

const moved = { ...game, options: [{ ...home, "배당": 2.2 }, away] };
assert.equal(canonicalOption(moved), null, "안전 배당 범위를 벗어나면 예전 추천을 유지하지 않는다");

const liveMoved = { ...game, _liveOddsChanged: true };
assert.equal(canonicalOption(liveMoved), null, "실시간 배당 revision 뒤에는 재계산 전 선택을 숨긴다");

const started = { ...game, _liveStarted: true };
assert.equal(canonicalOption(started), null, "실시간 중계가 시작되면 새 추천 생성은 막는다");
assert.equal(alignTodayRecommendations(today, [started]).candidates.length, 1,
  "경기 전에 저장한 오늘 추천은 시작 뒤 결과 추적용으로 유지한다");

const legacy = { ...game, decision_snapshot: undefined, 추천: away };
assert.equal(canonicalOption(legacy), null,
  "스냅샷이 없으면 현재 시장 최유력도 구매 후보로 복구하지 않는다");
assert.equal(canonicalPick(legacy, legacy.options, grades), null);
const legacyAligned = alignTodayRecommendations(today, [legacy]);
assert.equal(legacyAligned.candidates.length, 0,
  "스냅샷 없는 경기의 설명용 시장값은 오늘픽으로 들어가지 않는다");
assert.equal(legacyAligned.alignment.market_fallback_candidates, 0);

const staleOfferGame = {
  ...game,
  options: [{ ...home, offer_id: "off_home_changed" }, away],
};
assert.equal(canonicalOption(staleOfferGame), null,
  "offer revision이 어긋나면 브라우저가 현재 배당으로 새 픽을 만들지 않는다");
assert.equal(alignTodayRecommendations(today, [staleOfferGame]).candidates.length, 0,
  "offer revision 불일치 선택은 오늘픽에서도 제거한다");

const unallowlistedValidatedGame = {
  ...game,
  decision_snapshot: {
    ...game.decision_snapshot,
    decision_id: "dec_validated",
    probability: {
      market: 0.62, final: 0.68, ai_delta_applied: 0.06,
      basis: "validated_market_residual", residual_interval: [0.58, 0.72],
    },
    model: {
      status: "operational", validated_edge: true, policy_authorized: false,
      promotion_gate: "passed", operating_version: "residual-v1", artifact_hash: "abc",
    },
  },
};
const unallowlistedAligned = alignTodayRecommendations({
  ...today, candidates: [{ ...today.candidates[0], predicted_hit_prob: 0.91 }],
}, [unallowlistedValidatedGame]).candidates[0];
assert.equal(unallowlistedAligned.predicted_hit_prob, 0.62,
  "허용목록에 없는 artifact는 validated_edge 표식만으로 시장확률을 덮으면 안 된다");
assert.equal(unallowlistedAligned.probability_lower_bound, 0.62);
assert.equal(unallowlistedAligned.probability_interval, null);
assert.equal(unallowlistedAligned.uncertainty_source, "shin_market_fallback");
assert.equal(unallowlistedAligned.has_validated_edge, false);
assert.equal(unallowlistedAligned.policy_authorized, false);
assert.equal(unallowlistedAligned.decision_pipeline_applied, false);
assert.equal(unallowlistedAligned.decision_model, "residual-v1");

const policyGame = {
  ...game,
  decision_snapshot: {
    ...game.decision_snapshot,
    decision_id: "dec_policy",
    probability: {
      market: 0.62, final: 0.66, ai_delta_applied: 0.04,
      basis: "internal-context-blend-v2", residual_interval: null,
    },
    model: {
      status: "operational", validated_edge: false, policy_authorized: true,
      promotion_gate: "passed", operating_version: "internal-context-blend-v2",
    },
  },
};
const policyAligned = alignTodayRecommendations(today, [policyGame]).candidates[0];
assert.equal(policyAligned.predicted_hit_prob, 0.62,
  "정책 플래그만 있는 최종확률은 현재 시장확률을 덮어쓰면 안 된다");
assert.equal(policyAligned.probability_lower_bound, 0.62);
assert.equal(policyAligned.probability_source, "shin_market_fallback");
assert.equal(policyAligned.has_validated_edge, false,
  "정책 승인 확률을 통계 검증 우위로 바꾸면 안 된다");
assert.equal(policyAligned.policy_authorized, true);
assert.equal(policyAligned.decision_pipeline_applied, false);
assert.equal(policyAligned.selection_basis, "shin_market_fallback");
assert.equal(policyAligned.validated_uncertainty_available, false);

const policyCrossMarket = {
  ...policyGame,
  options: [
    { ...home, "시장확률": 0.60 },
    { selection_id: "sel_under", offer_id: "off_under", market: "언더오버",
      label: "2.5", "게임번호": "11", "선택": "언더", "배당": 1.70,
      "시장확률": 0.55, "최종확률": 0.72 },
  ],
  decision_snapshot: {
    ...policyGame.decision_snapshot,
    selection_id: "sel_under", offer_id: "off_under",
    probability: { ...policyGame.decision_snapshot.probability, market: 0.55, final: 0.72 },
  },
};
assert.equal(canonicalOption(policyCrossMarket), null,
  "정책 전용 스냅샷과 현재 시장 방향이 다르면 서버 원장 재판정을 기다린다");

const reissuedHome = { ...home, "게임번호": "110", selection_id: "sel_home_new",
  offer_id: "off_home_new" };
const reissuedAway = { ...away, "게임번호": "110", selection_id: "sel_away_new",
  offer_id: "off_away_new" };
const reissuedGame = {
  ...game, round: 8, date: "09.02(수) 08:40", home: "홈팀", away: "원정팀",
  decision_snapshot: {
    ...game.decision_snapshot,
    input_revision_hash: "b".repeat(64),
    selection_id: "sel_home_new",
    offer_id: "off_home_new",
  },
  options: [reissuedHome, reissuedAway],
};
const supersededToday = { candidates: [{
  round: 7, game_no: "10", date: "09.02(수) 08:40", home: "홈팀", away: "원정팀",
  market: "승패", market_label: "", sel: "홈", odds: 1.55,
  market_prob: .62, predicted_hit_prob: .62, is_market_favorite: true,
}] };
const reissuedAligned = alignTodayRecommendations(supersededToday, [
  { ...reissuedGame, round: 7, options: [home, away] }, reissuedGame,
]);
assert.equal(reissuedAligned.candidates.length, 1,
  "같은 실제 경기가 새 회차로 재발매되고 새 원장이 있으면 추천을 연결한다");
assert.equal(reissuedAligned.candidates[0].round, 8);
assert.equal(reissuedAligned.candidates[0].game_no, "110");

const reversalHome = { ...home, "모델확률": 0.40 };
const reversalAway = {
  ...away, "배당": 2.05, "시장확률": 0.42, "모델확률": 0.55,
};
const reversalGame = {
  ...game,
  decision_snapshot: undefined,
  options: [reversalHome, reversalAway],
};
assert.equal(canonicalOption(reversalGame), null,
  "스냅샷 없는 미검증 역배 경기도 브라우저가 시장픽을 새로 만들지 않는다");
const reversalToday = alignTodayRecommendations({
  ...today,
  year: 2026,
  odds_bins: [
    { bin: "1.5-1.8", roi: -0.1, n: 1000, hit: 0.6, grade: "B" },
    { bin: "1.8-2.2", roi: -0.12, n: 1000, hit: 0.45, grade: "C" },
  ],
}, [reversalGame]);
assert.deepEqual(reversalToday.candidates, []);

const liveFavoriteFlipped = {
  ...game,
  decision_snapshot: null,
  _liveOddsRecalculated: true,
  options: [
    { ...home, "배당": 2.1, "시장확률": 0.39 },
    { ...away, "배당": 1.55, "시장확률": 0.61, _liveOverround: 1.121 },
  ],
};
const flippedToday = alignTodayRecommendations({
  ...today,
  odds_bins: [{ bin: "1.5-1.8", roi: -0.1, n: 1000, hit: 0.6, grade: "B" }],
  candidates: [{
    ...today.candidates[0], predicted_hit_prob: 0.80,
    probability_source: "validated_final_probability", probability_lower_bound: 0.74,
    probability_interval: [0.74, 0.84], uncertainty_source: "validated_residual_interval",
    validated_uncertainty_available: true, has_validated_edge: true,
    policy_authorized: true, decision_pipeline_applied: true,
    decision_id: "stale", decision_model: "stale-model",
    decision_pipeline_status: "operational", decision_artifact_hash: "stale-hash",
    decision_evidence_ids: ["stale-evidence"],
  }],
}, [liveFavoriteFlipped]);
assert.equal(flippedToday.candidates.length, 0,
  "실시간 배당 revision 뒤 원장 기록 전에는 새 시장 방향도 오늘픽으로 쓰지 않는다");

const memberships = buildTodayMemberships({
  candidates: [alignedToday.candidates[0]],
  solo: alignedToday.candidates[0],
  plans: [
    { ok: true, target: 1.4, picks: [alignedToday.candidates[0]] },
    { ok: true, target: 2, picks: [alignedToday.candidates[0]] },
    { ok: false, target: 5, picks: [alignedToday.candidates[0]] },
  ],
});
const membership = memberships.get(selectionKey(home, game.round));
assert.equal(membership.recommended, true,
  "조합 포함 여부가 아니라 경기별 최종 후보 자체를 추천으로 표시한다");
assert.equal(membership.display.parts.length, 4,
  "추천 확률·배당·불확실성·데이터 상태를 복합 표시한다");
assert.equal(memberships.size, 1, "별도 후보 행이 아니라 같은 판정 키로 통합한다");

const rankedHighlights = dailyHighlightedSelections(Array.from({ length: 8 }, (_, index) => ({
  ...alignedToday.candidates[0],
  event_key: `event-${index}`,
  game_no: String(index),
  league: index < 4 ? "MLB" : index < 7 ? "J1리그" : "EFL챔",
  odds: 1.5 + index * 0.01,
  predicted_hit_prob: 0.62 - index * 0.01,
})));
assert.equal(rankedHighlights.length, 7, "오늘의 추천은 날짜별 리그 기본 세 픽을 남긴다");
assert.deepEqual(rankedHighlights.map((row) => Number(row.predicted_hit_prob.toFixed(2))),
  [0.62, 0.61, 0.60, 0.58, 0.57, 0.56, 0.55]);
const strongHighlights = dailyHighlightedSelections([0.72, 0.69, 0.66, 0.60].map((probability, index) => ({
  ...alignedToday.candidates[0], event_key: `strong-${index}`, game_no: `s-${index}`,
  league: "MLB", odds: 1.6 + index * 0.01, predicted_hit_prob: probability,
})));
assert.equal(strongHighlights.length, 4, "60% 이상 후보는 리그 기본 세 개를 넘어 추가한다");
assert.equal(dailyHighlightedSelections([{
  ...alignedToday.candidates[0], predicted_hit_prob: 0.549,
}]).length, 0, "최종 예상 적중확률 55% 미만은 하이라이트하지 않는다");
const explained = dailyRecommendationDecisions([
  ...strongHighlights,
  { ...alignedToday.candidates[0], event_key: "weak", game_no: "weak",
    league: "MLB", odds: 1.7, predicted_hit_prob: 0.54 },
  { ...alignedToday.candidates[0], event_key: "third", game_no: "third",
    league: "J1리그", odds: 1.7, predicted_hit_prob: 0.59 },
  { ...alignedToday.candidates[0], event_key: "j1-a", game_no: "j1-a",
    league: "J1리그", odds: 1.7, predicted_hit_prob: 0.64 },
  { ...alignedToday.candidates[0], event_key: "j1-b", game_no: "j1-b",
    league: "J1리그", odds: 1.7, predicted_hit_prob: 0.62 },
  { ...alignedToday.candidates[0], event_key: "j1-c", game_no: "j1-c",
    league: "J1리그", odds: 1.7, predicted_hit_prob: 0.61 },
]);
assert.match(explained.find((row) => row.selection.event_key === "strong-3").policyReason,
  /추가 기준 60%/);
assert.match(explained.find((row) => row.selection.event_key === "weak").policyReason,
  /55% 기준에 미달/);
assert.match(explained.find((row) => row.selection.event_key === "third").policyReason,
  /리그 내 4순위/);
assert.match(explained.find((row) => row.recommended).counterReason,
  /경기 기록을 확인/);
assert.ok(explained.every((row) => !/55%|60%|기본 추천|리그 내/.test(row.reason)));

const contextGame = { ...game, status: "경기전", year: 2026, date: "09.05(토) 18:00",
  home: "요미우리", away: "한신", league: "NPB", sport: "bs",
  form_home: { last10: "7승 3패" }, form_away: { last10: "4승 6패" } };
const contextCandidate = { ...today.candidates[0], date: contextGame.date,
  home: contextGame.home, away: contextGame.away, league: "NPB",
  match_reason: { reason: "캐시에 남은 반대 방향 설명" } };
const contextAligned = alignTodayRecommendations({ ...today, candidates: [contextCandidate] },
  [contextGame], Date.parse("2026-09-05T03:00:00Z"));
const contextDecision = dailyRecommendationDecisions(contextAligned.candidates)[0];
assert.equal(contextDecision.selection.sel, "홈");
assert.equal(contextDecision.selection.predicted_hit_prob, 0.62);
assert.match(contextDecision.reason, /요미우리 최근 10경기 7승 3패/);
assert.doesNotMatch(contextDecision.reason, /캐시에 남은|55%|기본 추천/);
const contextMember = buildTodayMemberships(contextAligned).get(selectionKey(contextDecision.selection));
assert.equal(contextMember.reason, contextDecision.reason, "Dashboard와 경기 카드는 동일한 경기 근거를 쓴다");

const nonDefaultMarket = {
  ...away, selection_id: "sel_total", offer_id: "off_total",
  market: "언더오버", label: "2.5", "선택": "언더",
};
const nonDefaultMemberships = new Map([
  [selectionKey(nonDefaultMarket, game.round), { targets: [3], solo: false }],
]);
assert.equal(
  todaySelectionForGame(nonDefaultMemberships, [home, away, nonDefaultMarket], game.round).option,
  nonDefaultMarket,
  "기본 경기 예측과 다른 시장의 오늘 추천도 카드에서 찾아 강조한다",
);

const datedCandidates = ["09.05(토) 18:00", "09.06(일) 02:00"].flatMap((date, dayIndex) =>
  [0.59, 0.58, 0.57, 0.56].map((p, index) => ({
    ...alignedToday.candidates[0], league: "MLB", date, year: 2026,
    event_key: `${dayIndex}-${index}`, game_no: `${dayIndex}-${index}`,
    predicted_hit_prob: p,
  })));
const datedHighlights = dailyHighlightedSelections(datedCandidates);
assert.equal(datedHighlights.length, 6, "오늘 3개와 내일 3개는 같은 리그 순위를 뺏지 않는다");
assert.equal(datedHighlights.filter((row) => row.date.startsWith("09.05")).length, 3);
datedCandidates[1].kickoff_at = "2026-09-05T09:00:00Z";
assert.equal(dailyHighlightedSelections(datedCandidates).length, 6,
  "ISO 킥오프와 프로토 날짜가 같은 날이면 같은 리그 슬롯을 공유한다");

const recoveryNow = Date.parse("2026-09-05T10:00:00+09:00");
const recordedGame = {
  ...game, date: "09.05(토) 18:00", year: 2026, status: "경기전",
  home: "홈팀", away: "원정팀", league: "KBO",
  decision_snapshot: { ...game.decision_snapshot, decision_id: "saved-revision" },
  prediction_record: { selection_id: home.selection_id, prediction_snapshot_id: "saved-revision",
    captured_at: "2026-09-05T09:00:00+09:00", odds: home.배당 },
};
const recoveredToday = alignTodayRecommendations({ candidates: [] }, [recordedGame], recoveryNow);
assert.equal(recoveredToday.candidates.length, 1, "오래된 today API가 비어도 최신 저장 픽은 후보로 연결한다");
assert.equal(recoveredToday.alignment.recovered_from_picks, 1);
assert.equal(recoveredToday.candidates[0].sel, "홈");
assert.equal(buildTodayMemberships(recoveredToday).size, 1);
for (const changed of [
  { prediction_record: null },
  { _liveOddsChanged: true },
  { _liveStarted: true },
  { status: "정산" },
  { date: "09.05(토) 09:00" },
  { date: "09.06(일) 12:00" },
  { prediction_record: { ...recordedGame.prediction_record, prediction_snapshot_id: "other" } },
  { prediction_record: { ...recordedGame.prediction_record, odds: 1.9 } },
  { prediction_record: { ...recordedGame.prediction_record, captured_at: "invalid" } },
]) {
  assert.equal(alignTodayRecommendations({ candidates: [] }, [{ ...recordedGame, ...changed }], recoveryNow)
    .candidates.length, 0, "미기록·다른 revision·시작한 경기를 새 추천으로 복구하지 않는다");
}
assert.equal(alignTodayRecommendations({ candidates: [] }, [
  { ...recordedGame, date: "09.06(일) 02:00" },
], recoveryNow).candidates.length, 1, "다음 날 오전 후보도 준비한다");

console.log("unified recommendation tests passed");
