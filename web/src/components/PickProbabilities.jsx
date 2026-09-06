

const percent = (value) => Number.isFinite(value) && value >= 0 && value <= 1
  ? `${(value * 100).toFixed(1)}%` : null;

export default function PickProbabilities({ openingProbability, estimate, phase = "live", message, compact = false }) {
  const current = phase === "live" ? percent(estimate?.probability) : null;
  const unavailable = phase === "upcoming" ? "시작 전" : phase === "finished" ? "경기 종료"
    : phase === "pending" ? "확인 중" : "계산 불가";
  return <span className={`pick-probabilities${compact ? " is-compact" : ""}`} aria-label="이 픽이 맞을 확률">
    <span className="pick-probability-values">
      <span><span>경기 전 적중 확률</span><b>{percent(openingProbability) ?? "기록 없음"}</b></span>
      <span><span>현재 적중 확률(추정)</span><b>{current ?? unavailable}</b></span>
    </span>
    <span className="pick-probability-note">{!current && (message || "경기 전 기록과 최신 점수가 필요합니다.")}</span>

  </span>;
}
