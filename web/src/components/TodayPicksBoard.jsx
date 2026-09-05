import { Card } from "./ui.jsx";
import { PHASE_LABEL } from "../lib/match-status.js";
import { trackTodayPicks } from "../lib/today-pick-tracking.js";

const percent = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "기록 없음";
const SOURCE_LABEL = { highlight: "오늘 추천 · 사전 기록", recorded: "사전 픽 · 추천 이력 미확인",
  current: "오늘 추천 · 경기 전" };

export function TodayPicksBoard({ games = [], today = null, now = Date.now() }) {
  const picks = trackTodayPicks({ games, today, now });
  return (
    <section className="mb-5 min-w-0" aria-label="오늘의 추천 픽">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="m-0 text-[17px] font-bold tracking-tight">오늘의 추천 픽 <span className="tnum text-ink3">{picks.length}</span></h2>
        <p className="m-0 text-[12px] text-ink3">사전 픽과 당시 배당을 경기 종료까지 확인합니다.</p>
      </div>
      {!picks.length ? <Card className="p-4 text-[13px] text-ink3">오늘 확인된 추천·사전 픽이 없습니다.</Card>
        : <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {picks.map((pick) => (
            <Card as="article" key={pick.key} className="min-w-0 p-3 sm:p-4" aria-label={`${pick.game.home} 대 ${pick.game.away} 사전 픽`}>
              <div className="flex flex-wrap items-center justify-between gap-1 text-[11px] text-ink3">
                <span>{pick.game.league} · <time dateTime={new Date(pick.kickoff).toISOString()}>{new Date(pick.kickoff + 9 * 3600000).toISOString().slice(11, 16)}</time></span>
                <span className={pick.phase === "live" ? "live-score-badge" : ""}>{PHASE_LABEL[pick.phase]}</span>
              </div>
              <h3 className="my-2 break-words text-[15px] font-semibold">{pick.game.home} <span className="font-normal text-ink3">vs</span> {pick.game.away}</h3>
              <p className="m-0 text-[11px] text-ink3">{SOURCE_LABEL[pick.source]}</p>
              <p className="mt-1 mb-3 break-words text-[14px] font-bold">{pick.option.market}{pick.option.label ? ` ${pick.option.label}` : ""} · {pick.option.선택}</p>
              <dl className="m-0 grid grid-cols-2 gap-3 border-t border-rule pt-3">
                <div><dt className="text-[11px] text-ink3">사전 확률</dt><dd className="tnum m-0 text-[19px] font-semibold">{percent(pick.openingProbability)}</dd></div>
                <div><dt className="text-[11px] text-ink3">현재 추정</dt><dd className="tnum m-0 text-[19px] font-semibold">{pick.estimate ? percent(pick.estimate.probability) : "—"}</dd></div>
              </dl>
              <p className="mt-2 mb-3 text-[11px] text-ink3">{pick.estimate ? "사전 확률에 현재 점수·경기 진행을 반영한 추정" : pick.estimateMessage}</p>
              <div className="border-t border-rule pt-2">
                <div className="flex items-center justify-between gap-2 text-[12px]"><span className="text-ink3">결과</span><b className={`result-badge is-${pick.outcome.state}`}>{pick.phase === "upcoming" ? "경기 전" : pick.outcome.label}</b></div>
                <div className="mt-1 flex items-center justify-between gap-2 text-[12px]"><span className="text-ink3">당시 배당</span><b className="tnum">{pick.originalOdds == null ? "기록 없음" : `${pick.originalOdds.toFixed(2)}배`}</b></div>
              </div>
            </Card>
          ))}
        </div>}
    </section>
  );
}

export default TodayPicksBoard;
