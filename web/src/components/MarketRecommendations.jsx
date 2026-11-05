import { recordedMarketOffers } from "../lib/recorded-market-offers.js";

// Only the current, unambiguous product can be attached to a recommendation.
export function currentMarketRecommendations(game, options = []) {
  const data = game?.market_recommendations;
  if (data?.version !== "multi-market-recommendations-v1" || !Array.isArray(data.items) || game?._liveOddsChanged) return [];
  const offers = recordedMarketOffers(options, game);
  const seen = new Set();
  return data.items.flatMap((item) => {
    if (!item || !["핸디캡", "언더오버"].includes(item.market) || !item.selection_id || !item.offer_id) return [];
    const matches = options.filter((o) => o.selection_id === item.selection_id && o.offer_id === item.offer_id);
    if (matches.length !== 1) return [];
    const option = matches[0];
    if (options.filter((o) => o.selection_id === item.selection_id).length !== 1 ||
      option.market !== item.market || (option.label || "") !== (item.label || "") ||
      option["선택"] !== item.selection || typeof item.line !== "number" || !Number.isFinite(item.line) ||
      (option.line == null || option.line === "" || Number(option.line) !== item.line) || Number(option["배당"]) !== item.odds) return [];
    const offer = offers.find((o) => o.gameNo === String(option["게임번호"]) && o.market === item.market && o.label === (item.label || ""));
    if (!offer || !["full", "full_game"].includes(offer.period) || !offer.settlementScope ||
        !offer.selections.some((s) => s.name === item.selection)) return [];
    const key = `${item.selection_id}|${item.offer_id}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ item, option }];
  });
}

const fraction = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1;
const percent = (n) => `${(n * 100).toFixed(1)}%`;
const probabilityAllowed = (item) => typeof item.probability_basis === "string" &&
  /model|market|모델|시장/i.test(item.probability_basis) && !/vote|share|투표|지지/i.test(item.probability_basis);
export const validationLabel = (item) => item.validation_status === "validated_profitable"
  ? "수익성 검증됨" : "연구 추천 · 수익성 미검증";

export default function MarketRecommendations({ recommendations, onSelect, selected = () => false }) {
  if (!recommendations.length) return null;
  return <section className="my-3 rounded border border-rule2 p-3" aria-label="시장별 자동 추천">
    <h3 className="m-0 text-[13px] font-semibold">핸디캡 · 언더오버 자동 추천</h3>
    {recommendations.map(({item, option}) => <article className="mt-2 rounded border border-rule2 p-2 text-[12px] leading-6" key={`${item.selection_id}|${item.offer_id}`}>
      <b>{item.market} {item.label} · {item.selection} · 배당 {Number(item.odds).toFixed(2)}배</b>
      <p className="my-1">{validationLabel(item)} · 기준점 {item.line} · 정산 {item.settlement_rule || option.settlement_scope}</p>
      <p className="my-1 text-ink3">모델 {item.model || "미제공"} · 출처 {Array.isArray(item.source_ids) && item.source_ids.length ? item.source_ids.join(", ") : "미제공"}
        {item.generated_at ? ` · 생성 ${item.generated_at}` : ""}</p>
      <p className="my-1 text-ink3">{probabilityAllowed(item) && fraction(item.probability) ? `예상 적중확률 ${percent(item.probability)} (${item.probability_basis})` : "예상 적중확률 미제공"}
        {fraction(item.p_push) ? ` · 무효 확률 ${percent(item.p_push)}` : " · 무효 확률 미제공"}
        {probabilityAllowed(item) && typeof item.estimated_roi === "number" && Number.isFinite(item.estimated_roi) ? ` · 예상 수익률 ${(item.estimated_roi * 100).toFixed(1)}% (베팅액 대비)` : " · 예상 수익률 미제공"}</p>
      {onSelect && <button type="button" className="bet-record-button" disabled={selected(option)} onClick={() => onSelect(option)}>{selected(option) ? "✓ 선택 목록에 담김" : "이 추천을 선택 목록에 담기"}</button>}
    </article>)}
  </section>;
}
