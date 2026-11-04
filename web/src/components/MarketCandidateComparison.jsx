import { useState } from "react";
import { recordedMarketOffers } from "../lib/recorded-market-offers.js";

const families = ["승패", "승무패", "핸디캡", "언더오버"];
const keyOf = (row) => JSON.stringify([String(row["게임번호"] ?? ""), row.market, row.label || "", row["선택"]]);

export function comparisonOffers(game, options = []) {
  const candidates = game?.market_candidates;
  const rows = candidates?.version === "tipster-market-candidates-v1"
    && candidates?.candidate_only === true && candidates?.affects_selection === false
    && candidates?.affects_probability === false && !game?._liveOddsChanged
    ? candidates.offers || [] : [];
  return recordedMarketOffers(options, game).filter((offer) => families.includes(offer.market))
    .map((offer) => ({ ...offer, candidate: rows.find((row) => row.game_no === offer.gameNo
      && row.market === offer.market && row.label === offer.label && row.period === "full_game"
      && row.settlement === `${offer.selections.length}way`
      && Number.isFinite(row.line) && options.some((o) => String(o["게임번호"]) === offer.gameNo
        && o.market === offer.market && o.line === row.line)) || null,
      selections: offer.selections.map((selection) => {
        const original = options.find((o) => String(o["게임번호"]) === offer.gameNo
          && o.market === offer.market && (o.label || "") === offer.label && o["선택"] === selection.name);
        const p = original?.["시장확률"];
        return { ...selection, original,
          actionable: original?.price_receipt_verified === true && selection.provenance.kind === "ledger_pick" && ["full", "full_game"].includes(offer.period)
            && Boolean(offer.settlementScope),
          marketProbability: typeof p === "number" && Number.isFinite(p) && p > 0 && p < 1 ? p : null };
      }),
    }));
}

export default function MarketCandidateComparison({ game, options = [], blocked = false, onSelect }) {
  const [family, setFamily] = useState("");
  const [chosen, setChosen] = useState(null);
  const gameKey = JSON.stringify([game?.year, game?.round, game?.sport, game?.league, game?.date, game?.home, game?.away]);
  const choiceKey = (row) => `${gameKey}:${keyOf(row)}`;
  const offers = comparisonOffers(game, options);
  const visible = offers.filter((offer) => !family || offer.market === family);
  const references = game?.market_candidates?.version === "tipster-market-candidates-v1"
    && game.market_candidates.candidate_only === true && game.market_candidates.affects_selection === false
    && game.market_candidates.affects_probability === false
    ? (game.market_candidates.references || []).filter((r) => r.reference_only === true
      && ["핸디캡", "언더오버"].includes(r.market) && (!family || family === r.market)
      && typeof r.source_line === "number" && Number.isFinite(r.source_line)
      && /^https:\/\/www\.goli\.tv\/board\/free\/\d+\/?$/.test(r.source_url || "")) : [];
  const chosenOption = offers.flatMap((offer) => offer.selections).find((s) => choiceKey(s.original) === chosen);
  return <section className="my-3 rounded border border-rule2 p-3" aria-label="시장별 픽 비교">
    <h3 className="m-0 text-[13px] font-semibold">승무패 · 핸디캡 · 언더오버 비교</h3>
    <p className="my-2 text-[11.5px] leading-6 text-ink3">최종 예측과 별도로 원하는 시장과 방향을 직접 비교할 수 있습니다. 픽스터의 지지율은 적중확률이 아니며, 다른 시장의 수익성은 아직 검증되지 않았습니다.</p>
    <div className="mb-2 flex flex-wrap gap-2" role="group" aria-label="비교할 시장">
      {["", ...families.filter((name) => offers.some((o) => o.market === name))].map((name) =>
        <button key={name} type="button" className="bet-record-button" aria-pressed={family === name} onClick={() => { setFamily(name); setChosen(null); }}>{name || "전체 시장"}</button>)}
    </div>
    {references.length > 0 && <section className="mb-3 rounded border border-rule2 p-2" aria-label="픽스터 원문 참고 후보">
      <h4 className="m-0 text-[12px] font-semibold">픽스터 원문 참고 후보 · 최종 예측 미반영</h4>
      {references.map((r) => <article key={`${r.author}|${r.family}`} className="my-2 text-[11.5px]">
        <p className="my-1"><b>{r.market} · {r.selected_team || (r.side === "over" ? "오버" : "언더")} {r.source_line > 0 && r.market === "핸디캡" ? "+" : ""}{r.source_line}{r.unit === "points" ? "점" : ""}</b></p>
        <p className="my-1 text-ink3">현재 상품 기준점{r.market === "핸디캡" ? " (홈 기준)" : ""}: {(r.current_lines || []).join(", ") || "확인 대기"}
          {game?._liveOddsChanged ? " · 상품 갱신 중" : r.status === "line_mismatch" ? " · 원문과 달라 같은 상품으로 연결하지 않음" : " · 정산 범위 미확인"}</p>
        <p className="my-1 text-ink3">{r.is_parlay_leg ? "조합글의 개별 의견 · " : ""}기간·정산 방식과 해당 가격의 실제 판매 여부는 미확인입니다.</p>
        <a href={r.source_url} target="_blank" rel="noopener noreferrer">픽스터 원문 보기</a>
        <span className="ml-2 text-ink3">수신 {new Date(r.observed_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST</span>
      </article>)}
    </section>}
    {!visible.length && <p role="status" className="text-[12px] text-ink3">비교할 완전한 상품 자료가 없습니다. 미발매·수집 누락·기준점 충돌 여부를 확인해야 합니다.</p>}
    <div className="grid gap-2 sm:grid-cols-2">
      {visible.map((offer) => <section key={offer.gameNo} className="rounded border border-rule2 p-2">
        <h4 className="m-0 text-[12px] font-semibold">{offer.gameNo} · {offer.market} {offer.label}</h4>
        <p className="my-1 text-[10.5px] text-ink3">{offer.settlementScope ? `정산 ${offer.settlementScope}` : "정산 범위 미확인"} · 개별 가격의 현재 판매 여부는 공식 발매처에서 확인</p>
        {offer.candidate?.status === "candidate" ? <p className="text-[11px]">픽스터 시장 후보: {offer.candidate.selection} · {offer.candidate.authors}명/발행처 · 수익성 미검증</p>
          : <p className="text-[11px] text-ink3">{game?.market_candidates?.status === "tipster_input_unavailable" ? "픽스터 자료 조회 실패 · 의견 유무 확인 대기"
            : offer.candidate ? "픽스터 의견 동률 또는 상충 · 방향 보류" : "기준점까지 일치하는 경기 전 픽스터 의견 없음"}</p>}
        <div className="flex flex-wrap gap-2">
          {offer.selections.map((s) => <button key={s.name} type="button" className="bet-record-button" aria-pressed={chosen === choiceKey(s.original)}
            onClick={() => setChosen(choiceKey(s.original))}>
            {s.name} · {s.odds.toFixed(2)}<small className="block">{s.marketProbability === null ? "시장확률 미산출" : `배당 기반 ${(s.marketProbability * 100).toFixed(1)}%`}</small>
          </button>)}
        </div>
      </section>)}
    </div>
    {chosenOption && <div className="mt-2 text-[12px]" role="status">
      내 비교 선택: {chosenOption.original.market} {chosenOption.original.label} · {chosenOption.name} · {chosenOption.odds.toFixed(2)}
      {onSelect && <button type="button" className="bet-record-button ml-2" disabled={blocked || !chosenOption.actionable} onClick={() => onSelect(chosenOption.original)}>내 선택 목록에 담기</button>}
      {!chosenOption.actionable && <p className="text-[11px] text-ink3">가격 수신 기록과 정산 범위 확인 전에는 비교만 가능합니다.</p>}
      {blocked && <p className="text-[11px] text-ink3">종료·시작 임박 또는 자료 갱신 상태에서는 새 사전 선택을 담지 않습니다.</p>}
    </div>}
  </section>;
}
