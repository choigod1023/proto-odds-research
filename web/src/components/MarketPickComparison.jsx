import { LEAGUE_HANDICAP_LABELS, resultLabel } from "./MarketRecommendations.jsx";

const MODELS = ["frontier_m0_actual", "frontier_m0_projected", "frontier_m1_projected"];
const FINAL = "frontier_consensus";
const INTERNATIONAL = "international-team-poisson-handicap-v1";
const LABELS = {
  frontier_m0_actual: "M0 실제 타순 학습",
  frontier_m0_projected: "M0 추정 타순 학습",
  frontier_m1_projected: "M1 선수 효과",
  market: "시장 최저 배당",
  frontier_tipster: "픽스터 방향 · 배당 기준",
  ...LEAGUE_HANDICAP_LABELS,
  [INTERNATIONAL]: "리그별 팀 득점 모델 · 연구 비교",
  [FINAL]: "세 모델 공통",
};
const BASIS = {
  market: "market_inverse_odds",
  frontier_tipster: "tipster_direction_market_odds",
  "kleague1-team-poisson-handicap-v1": "frozen_league_handicap_score_model",
  "kbl-team-margin-handicap-v1": "frozen_league_handicap_score_model",
  [FINAL]: "score_model_min",
};
const SIDES = {언더오버: ["언더", "오버"], 핸디캡: ["핸디홈", "핸디무", "핸디원정"]};
const number = (v) => typeof v === "number" && Number.isFinite(v);
const fraction = (v) => number(v) && v >= 0 && v <= 1;
const percent = (v) => `${(v * 100).toFixed(1)}%`;
const signed = (v) => `${v > 0 ? "+" : ""}${percent(v)}`;

function sameOffer(a, b) {
  return a.selection_id === b.selection_id && a.offer_id === b.offer_id && a.odds === b.odds;
}

function comparisonResult(item, arm, grade) {
  return grade?.selection_id === arm.selection_id && grade.offer_id === arm.offer_id
    && grade.model === arm.model && grade.market === item.market && grade.line === item.line
    && grade.settlement_rule === item.settlement_rule && Number(grade.odds) === arm.odds
    && ["hit", "miss", "void"].includes(grade.result) ? grade : null;
}

function validArm(item, arm, name, options, saved) {
  if (!arm || !arm.selection_id || !arm.offer_id || !SIDES[item.market]?.includes(arm.selection)
    || arm.probability_basis !== (BASIS[name] || "score_model")
    || arm.model !== (name === "market" ? "market-baseline-v1" : name)
    || !fraction(arm.probability) || !fraction(arm.p_push) || arm.p_push >= 1
    || !number(arm.odds) || arm.odds <= 1 || !number(arm.estimated_roi)
    || arm.estimated_roi < -1 || arm.estimated_roi > arm.odds - 1 + 1e-6) return false;
  if (saved) return true; // Arms are part of the validated frozen product snapshot.
  const matches = options.filter((o) => o?.selection_id === arm.selection_id);
  const o = matches[0];
  return matches.length === 1 && o.offer_id === arm.offer_id && o.market === item.market
    && (o.label || "") === (item.label || "") && o["선택"] === arm.selection
    && String(o["게임번호"]) === String(item.game_no) && o.line != null && o.line !== ""
    && Number(o.line) === item.line && Number(o["배당"]) === arm.odds
    && o.settlement_rule === item.settlement_rule && o.result_period === item.result_period;
}

export function comparisonRows(recommendations, options) {
  const rows = [];
  for (const {item, saved, result, comparisonVersion} of recommendations) {
    if (comparisonVersion !== "exact-market-frontier-v1") continue;
    const arms = item.comparisons || {};
    const valid = Object.keys(LABELS).filter((name) => validArm(item, arms[name], name, options, saved));
    for (const name of valid) {
      const arm = arms[name];
      if (name === FINAL && (!MODELS.every((m) => valid.includes(m) && sameOffer(arm, arms[m]))
        || arm.probability < .55 || arm.estimated_roi <= 0
        || Math.abs(arm.probability - Math.min(...MODELS.map((m) => arms[m].probability))) > 1e-6
        || Math.abs(arm.estimated_roi - Math.min(...MODELS.map((m) => arms[m].estimated_roi))) > 1e-6)) continue;
      rows.push({item, arm, name, saved, grade: saved ? comparisonResult(item, arm, result?.comparisons?.[name]) : null});
    }
  }
  const finals = rows.filter((r) => r.name === FINAL);
  return {rows: rows.filter((r) => r.name !== FINAL), final: finals.length === 1 ? finals[0] : null};
}

function historyText(history) {
  if (!history || !Number.isInteger(history.bets) || history.bets <= 0) return "정산 표본 없음";
  const hit = fraction(history.hit_rate) && history.decided > 0
    ? `${percent(history.hit_rate)} (${history.hits}/${history.decided})` : "적중률 미산출";
  return `${hit} · ROI ${number(history.roi) ? signed(history.roi) : "미산출"} · 정산 ${history.bets}건`;
}

export default function MarketPickComparison({ recommendations = [], options = [], performance, game }) {
  const {rows, final} = comparisonRows(recommendations, options);
  const hasConsensusModels = rows.some(({item, name}) => name === MODELS[0]
    && MODELS.slice(1).every((model) => rows.some((row) => row.item === item && row.name === model)));
  const showResult = rows.some(({saved}) => saved);
  const role = ({item, arm, name}) => arm.model === item.model && sameOffer(item, arm) ? "운영 픽"
    : name === "market" || name === "frontier_tipster" ? "참고 기준" : "연구 비교";
  const history = ({item, arm, name}) => historyText(performance?.comparison_by_context?.[
    [game?.sport, game?.league, item.market, item.settlement_rule, item.line, arm.selection, name].join("|")
  ]);
  const pickLabel = ({item, arm}) => `${item.market} ${item.label} · ${arm.selection}`;
  return <section aria-label="모델별 픽 비교" className="my-3 rounded border border-rule2 bg-panel p-3 text-[12px] leading-6">
    <h3 className="m-0 text-[13px] font-semibold">모델별 픽 비교 · 언더오버 / 핸디캡</h3>
    {final
      ? <p className="my-1"><b>세 모델 합의 픽 · 연구용 · {pickLabel(final)}</b>
        {" · "}{final.arm.odds.toFixed(2)}배 · 예상 적중확률 {percent(final.arm.probability)} · 기대 ROI {signed(final.arm.estimated_roi)}
        {final.saved && ` · 이 경기 ${resultLabel(final.grade, {compact: true})}`}
        <br /><span className="text-ink3">과거 실적: {history(final)}</span></p>
      : hasConsensusModels ? <p className="my-1">세 모델 공통 조건을 충족한 연구용 픽이 없습니다.</p>
      : !rows.length ? <p className="my-1">같은 상품의 경기 전 비교 자료가 없습니다.</p>
      : rows.every((row) => role(row) === "참고 기준")
        ? <p className="my-1">현재 확인된 비교 자료는 참고 기준입니다.</p> : null}
    {!!rows.length && <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left" aria-label="픽별 적중확률과 ROI">
        <thead className="text-ink3"><tr>{["픽 · 배당", "모델 / 기준", "예상 적중확률", "기대 ROI",
          ...(showResult ? ["이 경기 결과"] : []), "과거 적중률 · ROI"].map((label) =>
          <th key={label} scope="col" className="whitespace-nowrap border-b border-rule2 px-2 py-1">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={`${row.name}|${row.arm.offer_id}`} className="border-b border-rule2">
          <th scope="row" className="whitespace-nowrap px-2 py-1 font-medium">{pickLabel(row)} · {row.arm.odds.toFixed(2)}배</th>
          <td className="whitespace-nowrap px-2">{LABELS[row.name]}<span className="block text-ink3">{role(row)}</span></td>
          <td className="whitespace-nowrap px-2 tnum">{percent(row.arm.probability)}</td>
          <td className="whitespace-nowrap px-2 tnum">{signed(row.arm.estimated_roi)}</td>
          {showResult && <td className="whitespace-nowrap px-2">{row.saved ? resultLabel(row.grade, {compact: true}) : "—"}</td>}
          <td className="whitespace-nowrap px-2 tnum">{history(row)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    <p className="mb-0 text-ink3">예상값은 정상 정산을 가정한 미검증 추정치입니다. 픽스터 투표는 방향만 사용합니다.
      과거 실적은 같은 리그·시장·기준점·방향·정산 규칙의 모델별 사전 기록을 공식 결과로 정산한 값입니다.
      {hasConsensusModels && " 합의 픽은 같은 선택의 모델별 최저 확률과 기대 ROI를 기준으로 비교합니다."}</p>
  </section>;
}
