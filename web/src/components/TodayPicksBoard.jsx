import { Card } from "./ui.jsx";
import PickProbabilities from "./PickProbabilities.jsx";
import { gameStatusLabel } from "../lib/match-status.js";
import { trackTodayPicks } from "../lib/today-pick-tracking.js";
import { isOvernightGame } from "../lib/game-date-filter.js";

const SOURCE_LABEL = { highlight: "관심 픽 · 당시 추천 기록 보존", recorded: "사전 픽 · 추천 이력 미확인",
  current: "관심 픽 · 경기 전 · 수익성 미검증" };

export function TodayPicksBoard({ games = [], today = null, currentToday = null, now = Date.now(), onOpenGame }) {
  const picks = trackTodayPicks({ games, today, currentToday, now });
  const active = picks.filter((pick) => pick.phase !== "finished");
  const finished = picks.filter((pick) => pick.phase === "finished");
  const recommendations = picks.filter((pick) => pick.source !== "recorded").length;
  return (
    <section className="mb-5 min-w-0" aria-label="관심 픽과 검증된 추천">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="m-0 text-[17px] font-bold tracking-tight">관심 픽 <span className="tnum text-ink3">{recommendations}</span></h2>
        <p className="m-0 text-[12px] text-ink3">관심 픽은 기존 자격 기준을 통과한 후보이며 수익성이 검증된 추천이 아닙니다. 당시 추천 기록은 변경하지 않습니다.</p>
      </div>
      <p className="mb-3 text-[12px] text-ink3">관심 기준: 예상 적중 55% 이상 · 배당 1.50 이상 · 상한 없음. 조건 미달 경기는 채우지 않습니다.</p>
      <Card className="mb-3 p-4 text-[13px]" aria-label="검증된 추천">
        <h3 className="m-0 font-semibold">검증된 추천 0개 · 검증 대기</h3>
        <p>현재 수익성과 안정성 검증을 통과한 정책은 없습니다. 높은 확률이나 과거 적중만으로 승격하지 않습니다.</p>
        <p>모의 운용 기준: 2폴 · 회당 1만 원 · 주 최대 2회/3회 비교 · 월 손실 한도 5만 원. 미정산 금액도 한도에 포함하며 실매수는 하지 않습니다.</p>
        <a href="research.html">검증 방식과 모의 운용 한계 보기 →</a>
      </Card>
      {!picks.length && <Card className="p-4 text-[13px] text-ink3">오늘 확인된 추천·사전 픽이 없습니다.</Card>}
      <PickCards picks={active} now={now} onOpenGame={onOpenGame} />
      {finished.length > 0 && <details className="mt-3 rounded border border-rule p-3">
        <summary className="cursor-pointer text-[13px] font-semibold">종료된 사전 픽 {finished.length}개 · 적중 {finished.filter((pick) => pick.outcome.state === "hit").length} · 적중실패 {finished.filter((pick) => pick.outcome.state === "miss").length} · 결과와 당시 배당 보기</summary>
        <div className="mt-3"><PickCards picks={finished} onOpenGame={onOpenGame} /></div>
      </details>}
    </section>
  );
}

function PickCards({ picks, now, onOpenGame }) {
  return <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {picks.map((pick) => (
            <Card as="article" key={pick.key} className="today-pick-card min-w-0 p-3 sm:p-4" aria-label={`${pick.game.home} 대 ${pick.game.away} 사전 픽`}>
              {onOpenGame && <button type="button" className="today-pick-open" aria-haspopup="dialog"
                aria-label={`${pick.game.home} 대 ${pick.game.away} 관심 픽 경기정보 열기`}
                onClick={() => onOpenGame(pick.game)}><span>경기정보 보기 ↗</span></button>}
              <div className="flex flex-wrap items-center justify-between gap-1 text-[11px] text-ink3">
                <span>{pick.game.league} · <time dateTime={new Date(pick.kickoff).toISOString()}>{new Date(pick.kickoff + 9 * 3600000).toISOString().slice(11, 16)}</time></span>
                <span className={pick.phase === "live" ? "live-score-badge" : ""}>{gameStatusLabel(pick.game, pick.game._liveState, now)}</span>
              </div>
              <h3 className="my-2 break-words text-[15px] font-semibold">{pick.game.home} <span className="font-normal text-ink3">vs</span> {pick.game.away}</h3>
              {isOvernightGame(pick.game, now) && <p className="m-0 text-[11px] text-ink3">전날 시작 · 저장된 사전 픽 계속 추적</p>}
              {pick.phase === "live" && <p className="tnum my-2 text-[16px] font-bold">
                {pick.game._liveState?.home_score ?? "—"} : {pick.game._liveState?.away_score ?? "—"}
                <small className="ml-2 text-[12px] font-normal">{pick.game._liveState?.status_text || "진행 중"}</small>
              </p>}
              <p className="m-0 text-[11px] text-ink3">{SOURCE_LABEL[pick.source]}</p>
              <p className="mt-1 mb-3 break-words text-[14px] font-bold">{pick.option.market}{pick.option.label ? ` ${pick.option.label}` : ""} · {pick.option.선택}</p>
              <PickProbabilities openingProbability={pick.openingProbability} estimate={pick.estimate}
                phase={pick.phase} message={pick.estimateMessage} />
              <div className="border-t border-rule pt-2">
                <div className="flex items-center justify-between gap-2 text-[12px]"><span className="text-ink3">결과</span><b className={`result-badge is-${pick.outcome.state}`}>{pick.phase === "upcoming" ? "경기 전" : pick.phase === "live" ? "진행 중" : pick.outcome.label}</b></div>
                <div className="mt-1 flex items-center justify-between gap-2 text-[12px]"><span className="text-ink3">당시 배당</span><b className="tnum">{pick.originalOdds == null ? "기록 없음" : `${pick.originalOdds.toFixed(2)}배`}</b></div>
              </div>
            </Card>
          ))}
        </div>;
}

export default TodayPicksBoard;
