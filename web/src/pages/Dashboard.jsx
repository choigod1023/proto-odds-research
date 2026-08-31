import { useEffect, useMemo, useState } from "react";
import { Nav } from "../components/ui.jsx";
import ReceiptOcr from "../components/ReceiptOcr.jsx";
import { appendProbabilityHistory, estimateLiveProbability, liveKey,
  readBetLedger, removeBet, settleBet, writeBetLedger } from "../lib/bet-ledger.js";
import { usePolledData } from "../lib/poll.js";

const LIVE_URL = "https://proto-odds-collector.fly.dev/live_scores.json";
const PICKS_URL = "https://proto-odds-collector.fly.dev/picks_v2.json";
const pct = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "–";
const money = (value) => `${Math.round(value).toLocaleString("ko-KR")}원`;

function useLiveScores() {
  const [data, setData] = useState(null);
  useEffect(() => {
    let stopped = false;
    const load = () => fetch(`${LIVE_URL}?${Date.now()}`).then((response) => response.ok ? response.json() : null)
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

function BetCard({ bet, live, onRemove }) {
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
      <footer>
        <span>{estimate.basis === "score_time_estimate" ? "점수·남은 시간 기반 상황 추정치"
          : estimate.basis === "final_score" ? "최종 점수 기준"
            : estimate.basis === "live_score_missing" ? "진행 시간 부족 · 구매 당시 확률 유지" : "구매 당시 시장확률"}</span>
        <button type="button" onClick={() => onRemove(bet.id)}>기록 삭제</button>
      </footer>
    </article>
  );
}

export default function Dashboard() {
  const [bets, setBets] = useState(() => readBetLedger());
  const liveData = useLiveScores();
  const { data: pickSources } = usePolledData({
    picks: PICKS_URL,
    staticPicks: "data/picks_v2.json",
  }, 60000);
  const picks = pickSources.picks || pickSources.staticPicks;
  const index = useMemo(() => liveIndex(liveData), [liveData]);
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

  const totals = bets.reduce((result, bet) => {
    result.stake += Number(bet.stake) || 0;
    const live = index.get(liveKey(bet.game));
    const estimate = estimateLiveProbability(bet, live);
    if (live?.finished) {
      const outcome = settleBet(bet, live);
      if (outcome) result.settled += 1;
      if (outcome === "hit") result.profit += bet.stake * (bet.purchaseOdds - 1);
      if (outcome === "miss") result.profit -= bet.stake;
    }
    return result;
  }, { stake: 0, settled: 0, profit: 0 });

  return (
    <main className="mx-auto max-w-[1180px] px-5 pb-20">
      <Nav current="dashboard.html" />
      <header className="market-header">
        <div><h1>내 베팅 대시보드</h1><p>내가 구매한 단일 경기의 확률 변화와 실시간 진행, 확정 손익을 추적합니다.</p></div>
        <div className="market-meta">15초마다 실시간 점수 확인 · 이 브라우저에 저장</div>
      </header>
      <ReceiptOcr games={picks?.live || []} onImported={() => setBets(readBetLedger())} />
      <section className="dashboard-summary">
        <div><small>저장한 베팅</small><b>{bets.length}건</b></div>
        <div><small>총 투입금</small><b>{money(totals.stake)}</b></div>
        <div><small>정산 완료</small><b>{totals.settled}건</b></div>
        <div><small>확정 손익</small><b className={totals.profit < 0 ? "text-sev3" : ""}>{totals.profit >= 0 ? "+" : ""}{money(totals.profit)}</b></div>
      </section>
      <div className="dashboard-notice">
        현재 확률은 구매 당시 시장확률을 실시간 점수와 남은 시간으로 이동시킨 <b>상황 추정치</b>입니다.
        검증된 인플레이 베팅 모델이나 실시간 구매 추천이 아닙니다.
      </div>
      <section className="dashboard-bet-list">
        {bets.map((bet) => <BetCard key={bet.id} bet={bet} live={index.get(liveKey(bet.game))}
          onRemove={(id) => { removeBet(id); setBets(readBetLedger()); }} />)}
        {!bets.length && <div className="dashboard-empty">
          <b>저장한 베팅이 없습니다</b>
          <p>경기 분석에서 실제로 구매한 선택지의 ‘베팅 기록’ 버튼을 눌러 추가하세요.</p>
          <a href="markets.html">경기 분석으로 이동</a>
        </div>}
      </section>
    </main>
  );
}
