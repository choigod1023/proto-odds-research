const MODELS = ["frontier_m0_actual", "frontier_m0_projected", "frontier_m1_projected"];
const FINAL = "frontier_consensus";
const LABELS = {
  frontier_m0_actual: "M0 실제 타순 학습",
  frontier_m0_projected: "M0 추정 타순 학습",
  frontier_m1_projected: "M1 선수 효과",
  market: "시장 최저 배당",
  frontier_tipster: "픽스터 방향 · 배당 기준",
  [FINAL]: "세 모델 공통",
};
const BASIS = {
  market: "market_inverse_odds",
  frontier_tipster: "tipster_direction_market_odds",
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
  for (const {item, saved, comparisonVersion} of recommendations) {
    if (comparisonVersion !== "exact-market-frontier-v1") continue;
    const arms = item.comparisons || {};
    const valid = Object.keys(LABELS).filter((name) => validArm(item, arms[name], name, options, saved));
    for (const name of valid) {
      const arm = arms[name];
      if (name === FINAL && (!MODELS.every((m) => valid.includes(m) && sameOffer(arm, arms[m]))
        || arm.probability < .55 || arm.estimated_roi <= 0
        || Math.abs(arm.probability - Math.min(...MODELS.map((m) => arms[m].probability))) > 1e-6
        || Math.abs(arm.estimated_roi - Math.min(...MODELS.map((m) => arms[m].estimated_roi))) > 1e-6)) continue;
      rows.push({item, arm, name});
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
  const history = ({item, arm, name}) => historyText(performance?.comparison_by_context?.[
    [game?.sport, game?.league, item.market, item.settlement_rule, item.line, arm.selection, name].join("|")
  ]);
  const pickLabel = ({item, arm}) => `${item.market} ${item.label} · ${arm.selection}`;
  return <section aria-label="모델별 픽 비교" className="my-3 rounded border border-rule2 bg-panel p-3 text-[12px] leading-6">
    <h3 className="m-0 text-[13px] font-semibold">모델별 픽 비교 · 언더오버 / 핸디캡</h3>
    {final
      ? <p className="my-1"><b>지표 기준 최종 픽 · 비교용: {pickLabel(final)}</b>
        {" · "}{final.arm.odds.toFixed(2)}배 · 예상 적중확률 {percent(final.arm.probability)} · 기대 ROI {signed(final.arm.estimated_roi)}
        <br /><span className="text-ink3">과거 실적: {history(final)}</span></p>
      : <p className="my-1"><b>지표 기준 최종 픽 보류</b> · {rows.length
        ? "세 모델이 같은 픽에서 적중확률 55% 이상과 기대 ROI 양수를 모두 충족한 단일 후보가 없습니다."
        : "같은 상품의 경기 전 비교 자료가 없습니다."}</p>}
    {!!rows.length && <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left" aria-label="픽별 적중확률과 ROI">
        <thead className="text-ink3"><tr>{["픽 · 배당", "모델 / 기준", "예상 적중확률", "기대 ROI", "과거 적중률 · ROI"].map((label) =>
          <th key={label} scope="col" className="whitespace-nowrap border-b border-rule2 px-2 py-1">{label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={`${row.name}|${row.arm.offer_id}`} className="border-b border-rule2">
          <th scope="row" className="whitespace-nowrap px-2 py-1 font-medium">{pickLabel(row)} · {row.arm.odds.toFixed(2)}배</th>
          <td className="whitespace-nowrap px-2">{LABELS[row.name]}</td>
          <td className="whitespace-nowrap px-2 tnum">{percent(row.arm.probability)}</td>
          <td className="whitespace-nowrap px-2 tnum">{signed(row.arm.estimated_roi)}</td>
          <td className="whitespace-nowrap px-2 tnum">{history(row)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    <p className="mb-0 text-ink3">예상값은 정상 정산을 가정한 미검증 추정치입니다. 픽스터 투표는 방향만 사용합니다.
      과거 실적은 같은 리그·시장·기준점·방향·정산 규칙의 모델별 사전 기록을 공식 결과로 정산한 값입니다.
      최종 픽은 세 모델의 확률과 기대 ROI 중 각각 가장 낮은 값으로 비교합니다.
      지표 기준 픽은 기존 자동 추천과 별도로 표시합니다.</p>
  </section>;
}
