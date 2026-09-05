import { liveMatchProgress } from "../lib/live-progress.js";

export function LiveProgress({ game, live = game?._liveState, now = Date.now(), compact = false }) {
  const progress = liveMatchProgress(game, live, now);
  if (!progress || progress.state === "finished") return null;
  const known = Number.isFinite(progress.percent);
  const description = `${progress.label} · ${progress.basis}`;
  return <span className={`match-progress${compact ? " is-compact" : ""} is-${progress.state}`}
    aria-label="경기 진행률" title={`${description}. 적중확률이 아닙니다.`}>
    <span className="match-progress-caption">
      <span>경기 진행{known ? ` 약 ${progress.percent}%` : " · 비율 확인 불가"}</span>
      <span>{progress.label}</span>
    </span>
    {known && <span className="match-progress-track" role="progressbar" aria-label="경기 진행률"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-valuetext={description}>
      <span style={{ width: `${progress.percent}%` }} />
    </span>}
    <small className="match-progress-note">{progress.basis}</small>
  </span>;
}
