import { useEffect, useMemo, useState } from "react";
import { Nav } from "../components/ui.jsx";
import ReceiptOcr from "../components/ReceiptOcr.jsx";
import GameInfoModal from "../components/GameInfoModal.jsx";
import RecordMatchDetails from "../components/RecordMatchDetails.jsx";
import { appendProbabilityHistory, estimateLiveProbability, groupBetTickets, liveKey,
  readBetLedger, removeBet, removeTicket, settleBet, writeBetLedger } from "../lib/bet-ledger.js";
import { usePolledData } from "../lib/poll.js";

const LIVE_URL = "https://proto-odds-collector.fly.dev/api/live-scores";
const PICKS_URL = "https://proto-odds-collector.fly.dev/api/picks";
const pct = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "–";
const money = (value) => `${Math.round(value).toLocaleString("ko-KR")}원`;

function useLiveScores() {
  const [data, setData] = useState(null);
  useEffect(() => {
    let stopped = false;
    const load = () => fetch(`${LIVE_URL}?${Date.now()}`, {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache" },
    }).then((response) => response.ok ? response.json() : null)
      .then((value) => { if (!stopped && value) setData(value); }).catch(() => {});
    load();
    const timer = setInterval(load, 15000);
    return () => { stopped = true; clearInterval(timer); };
  }, []);
  return data;
}

function liveIndex(data) {
  const index = new Map();
  (data?.games || []).forEach((game) => {
    const homes = [game.home, ...(game.home_alias || [])].filter(Boolean);
    const aways = [game.away, ...(game.away_alias || [])].filter(Boolean);
    homes.forEach((home) => aways.forEach((away) => index.set(`${home}|${away}|${game.md}`, game)));
  });
  return index;
}

function ProbabilityTrack({ bet, estimate }) {
  const start = Number(bet.openingProbability);
  const current = Number(estimate.probability);
  const left = Math.min(start, current) * 100;
  const width = Math.max(1.5, Math.abs(current - start) * 100);
  return (
    <div className="probability-track" aria-label={`구매 당시 ${pct(start)}, 현재 ${pct(current)}`}>
      <div className="probability-track-line"><i style={{ left: `${left}%`, width: `${width}%` }} /></div>
      <div><span style={{ left: `${start * 100}%` }}>구매</span><b style={{ left: `${current * 100}%` }}>현재</b></div>
    </div>
  );
}

export function BetCard({ bet, live, onRemove, onOpen }) {
  const estimate = estimateLiveProbability(bet, live);
  const change = Number.isFinite(estimate.probability) && Number.isFinite(bet.openingProbability)
    ? estimate.probability - bet.openingProbability : null;
  const final = live?.finished;
  const result = settleBet(bet, live);
  const profit = final ? (result === "hit" ? bet.stake * (bet.purchaseOdds - 1)
    : result === "void" ? 0 : result === "miss" ? -bet.stake : null) : null;
  const status = live?.cancelled ? "취소" : live?.postponed ? "연기"
    : final ? "종료" : live?.status === "STARTED" ? "LIVE" : "경기 전";
  return (
    <article className={`dashboard-bet-card is-${status === "LIVE" ? "live" : final ? "finished" : "upcoming"}`}>
      <header>
        <div><span>{bet.game.league} · {bet.game.date}</span><h2>{bet.game.home} vs {bet.game.away}</h2></div>
        <strong>{status}</strong>
      </header>
      <div className="dashboard-bet-pick">
        <div><small>내 픽</small><b>{bet.selection.market}{bet.selection.label ? ` ${bet.selection.label}` : ""} · {bet.selection.choice}</b></div>
        <div><small>구매</small><b>{Number(bet.purchaseOdds).toFixed(2)}배 · {money(bet.stake)}</b></div>
        <div><small>{final ? "확정 손익" : "적중 시 순이익"}</small><b className={profit < 0 ? "text-sev3" : ""}>{final
          ? profit == null ? "판정 확인" : `${profit >= 0 ? "+" : ""}${money(profit)}`
          : `+${money(bet.stake * (bet.purchaseOdds - 1))}`}</b></div>
      </div>
      {live && <div className="dashboard-live-state">
        <strong>{live.home_score ?? "–"} : {live.away_score ?? "–"}</strong>
        <span>{live.status_text || status}</span>
      </div>}
      <div className="dashboard-probability">
        <div><small>구매 당시</small><b>{pct(bet.openingProbability)}</b></div>
        <div><small>현재 상황 추정</small><b>{pct(estimate.probability)}</b></div>
        <div><small>변화</small><b className={change < 0 ? "text-sev3" : ""}>{change == null ? "–" : `${change >= 0 ? "+" : ""}${(change * 100).toFixed(1)}%p`}</b></div>
      </div>
      {Number.isFinite(estimate.probability) && <ProbabilityTrack bet={bet} estimate={estimate} />}
      {bet.selectionSnapshot && <details className="saved-snapshot"><summary>선택 당시 기록 보기</summary>
        <p>선택 시각 {new Date(bet.selectionSnapshot.selectedAt).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })} KST</p>
        <p>선택 당시 배당 {Number(bet.selectionSnapshot.option?.배당).toFixed(2)}배 · 구매 기록 배당 {Number(bet.purchaseOdds).toFixed(2)}배</p>
        <p>당시 확률 {pct(bet.openingProbability)} · 배당 기준 또는 보존된 사전 확률</p>
        {(bet.selectionSnapshot.performance?.facts || []).map((fact) => <p key={fact}>{fact}</p>)}

      </details>}
      <footer>
        <button type="button" aria-haspopup="dialog" aria-label={`${bet.game.home} 대 ${bet.game.away} 경기정보 열기`} onClick={() => onOpen(bet.id)}>경기 상세 보기</button>

        <button type="button" onClick={() => onRemove(bet.id)}>기록 삭제</button>
      </footer>
    </article>
  );
}

export function ComboCard({ group, index, onRemove, onOpen }) {
  const rows = group.bets.map((bet) => {
    const live = index.get(liveKey(bet.game));
    return { bet, live, estimate: estimateLiveProbability(bet, live), outcome: settleBet(bet, live) };
  });
  const currentProbability = rows.every((row) => Number.isFinite(row.estimate.probability))
    ? rows.reduce((value, row) => value * row.estimate.probability, 1) : null;
  const openingProbability = rows.every((row) => Number.isFinite(row.bet.openingProbability))
    ? rows.reduce((value, row) => value * row.bet.openingProbability, 1) : null;
  const outcomes = rows.map((row) => row.outcome);
  const outcome = outcomes.includes("miss") ? "miss"
    : outcomes.length && outcomes.every((value) => value === "hit" || value === "void") ? "hit" : null;
  const isLive = rows.some((row) => row.live?.status === "STARTED" && !row.live?.finished);
  const profit = outcome === "hit" ? Number(group.ticket.expectedPayout) - Number(group.ticket.stake)
    : outcome === "miss" ? -Number(group.ticket.stake) : null;
  return (
    <article className={`dashboard-ticket-card ${isLive ? "is-live" : outcome ? "is-finished" : ""}`}>
      <header><div><small>{rows.length}폴더 조합</small><h2>조합 베팅 티켓</h2></div>
        <strong>{isLive ? "LIVE" : outcome === "hit" ? "적중" : outcome === "miss" ? "실패" : "진행 대기"}</strong></header>
      <div className="dashboard-ticket-summary">
        <div><small>조합배당</small><b>{Number(group.ticket.combinedOdds).toFixed(2)}배</b></div>
        <div><small>투입금</small><b>{money(group.ticket.stake)}</b></div>
        <div><small>예상적중금</small><b>{money(group.ticket.expectedPayout)}</b></div>
        <div><small>{outcome ? "확정 손익" : "현재 티켓 확률"}</small><b>{outcome ? `${profit >= 0 ? "+" : ""}${money(profit)}` : pct(currentProbability)}</b></div>
      </div>
      <div className="dashboard-ticket-legs">{rows.map(({ bet, live, estimate, outcome: legOutcome }, position) => <div key={bet.id}>
        <span className="ticket-leg-number">{position + 1}</span>
        <span><b>{bet.game.home} vs {bet.game.away}</b><small>{bet.selection.market}{bet.selection.label ? ` ${bet.selection.label}` : ""} · {bet.selection.choice} · {Number(bet.purchaseOdds).toFixed(2)}배</small></span>
        <span className="ticket-leg-live"><b>{live ? `${live.home_score ?? "–"}:${live.away_score ?? "–"}` : "경기 전"}</b><small>{live?.status_text || ""}</small></span>
        <span className="ticket-leg-probability"><b>{pct(estimate.probability)}</b><small>{legOutcome === "hit" ? "적중" : legOutcome === "miss" ? "실패" : "현재 추정"}</small></span>
        <button type="button" className="record-detail-button" aria-haspopup="dialog" aria-label={`${bet.game.home} 대 ${bet.game.away} 경기정보 열기`} onClick={() => onOpen(bet.id)}>경기 상세</button>
      </div>)}</div>
      <footer>
        <button type="button" onClick={() => onRemove(group.id)}>티켓 삭제</button></footer>
    </article>
  );
}

export default function Dashboard() {
  const [bets, setBets] = useState(() => readBetLedger());
  const [openedBetId, setOpenedBetId] = useState(null);
  const openedBet = bets.find((bet) => bet.id === openedBetId);
  const liveData = useLiveScores();
  const { data: pickSources } = usePolledData({
    picks: PICKS_URL,
  }, 60000);
  const receiptGames = useMemo(() => {
    const seen = new Set();
    return [pickSources.picks].flatMap((source) => source?.live || []).filter((game) => {
      const key = `${game.round}|${game.home}|${game.away}|${game.date}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }, [pickSources.picks]);
  const index = useMemo(() => liveIndex(liveData), [liveData]);
  const groups = useMemo(() => groupBetTickets(bets), [bets]);
  useEffect(() => {
    const refresh = () => setBets(readBetLedger());
    window.addEventListener("storage", refresh);
    window.addEventListener("proodd:bet-ledger", refresh);
    return () => { window.removeEventListener("storage", refresh); window.removeEventListener("proodd:bet-ledger", refresh); };
  }, []);
  useEffect(() => {
    if (!liveData || !bets.length) return;
    const next = bets.map((bet) => {
      const live = index.get(liveKey(bet.game));
      return appendProbabilityHistory(bet, estimateLiveProbability(bet, live), live);
    });
    if (JSON.stringify(next) !== JSON.stringify(bets)) {
      writeBetLedger(next);
      setBets(next);
    }
  }, [liveData, index]); // 원장 자체 변경은 사용자 이벤트에서 별도로 반영

  const totals = groups.reduce((result, group) => {
    result.stake += Number(group.ticket.stake) || 0;
    const outcomes = group.bets.map((bet) => settleBet(bet, index.get(liveKey(bet.game))));
    const outcome = outcomes.includes("miss") ? "miss"
      : outcomes.length && outcomes.every((value) => value === "hit" || value === "void") ? "hit" : null;
    if (outcome) result.settled += 1;
    if (outcome === "hit") result.profit += Number(group.ticket.expectedPayout) - Number(group.ticket.stake);
    if (outcome === "miss") result.profit -= Number(group.ticket.stake);
    return result;
  }, { stake: 0, settled: 0, profit: 0 });

  return (
    <main className="mx-auto max-w-[1180px] px-5 pb-20">
      <Nav current="dashboard.html" />
      <header className="market-header">
        <div><h1>내 베팅 대시보드</h1></div>

      </header>
      <ReceiptOcr games={receiptGames} onImported={() => setBets(readBetLedger())} />
      <section className="dashboard-summary">
        <div><small>저장한 티켓</small><b>{groups.length}장 · {bets.length}픽</b></div>
        <div><small>총 투입금</small><b>{money(totals.stake)}</b></div>
        <div><small>정산 완료</small><b>{totals.settled}건</b></div>
        <div><small>확정 손익</small><b className={totals.profit < 0 ? "text-sev3" : ""}>{totals.profit >= 0 ? "+" : ""}{money(totals.profit)}</b></div>
      </section>

      <section className="dashboard-bet-list">
        {groups.map((group) => group.bets.length > 1
          ? <ComboCard key={group.id} group={group} index={index} onOpen={setOpenedBetId} onRemove={(id) => { removeTicket(id); setBets(readBetLedger()); }} />
          : <BetCard key={group.id} bet={group.bets[0]} live={index.get(liveKey(group.bets[0].game))}
              onOpen={setOpenedBetId} onRemove={(id) => { removeBet(id); setBets(readBetLedger()); }} />)}
        {!bets.length && <div className="dashboard-empty">
          <b>저장한 베팅이 없습니다</b>

          <a href="markets.html">경기 분석으로 이동</a>
        </div>}
      </section>
      {openedBet && <GameInfoModal title={`${openedBet.game.home} vs ${openedBet.game.away}`} onClose={() => setOpenedBetId(null)}>
        <RecordMatchDetails bet={openedBet} live={index.get(liveKey(openedBet.game))} />
      </GameInfoModal>}
    </main>
  );
}
