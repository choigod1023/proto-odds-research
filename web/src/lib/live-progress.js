const numeric = (value) => typeof value === "number" || (typeof value === "string" && value.trim())
  ? Number(value) : NaN;
const TERMINAL = new Set(["RESULT", "END", "ENDED", "FINAL", "FINISHED"]);

/** Display-only match progress. Never infer it from a pick, score, or wall-clock since kickoff. */
export function liveMatchProgress(game, live = game?._liveState, now = Date.now()) {
  if (!live) return null;
  const status = String(live.status || "").toUpperCase();
  const label = live.cancelled ? "경기 취소" : live.postponed ? "경기 연기" : String(live.status_text || "진행 중");
  const interrupted = live.cancelled || live.postponed || /CANCEL|POSTPONE|SUSPEND|DELAY/.test(status)
    || /취소|연기|중단|우천|지연/.test(label);
  if (!interrupted && (live.finished || TERMINAL.has(status) || game?.status === "정산")) {
    return { state: "finished", percent: 100, label: "경기 종료", basis: "종료 확인" };
  }
  if (!interrupted && !["STARTED", "IN_PROGRESS", "LIVE", "PLAYING"].includes(status)) return null;
  const observed = Date.parse(live.observed_at || game?._liveFeedAt || "");
  if (!Number.isFinite(observed) || observed > Number(now) + 5000) {
    return { state: "unknown", percent: null, label, basis: "중계 시각 확인 중" };
  }
  if (live.stale === true || Number(now) - observed > 10 * 60 * 1000) {
    return { state: "stale", percent: null, label, basis: "중계 갱신 지연 · 마지막 진행 정보" };
  }
  if (interrupted) return { state: "interrupted", percent: null, label, basis: "경기 중단 · 진행률 보류" };
  const result = (fraction, basis, stage = label) => ({ state: "live",
    percent: Math.min(99, Math.max(0, Math.floor(fraction * 100))), label: stage, basis });
  const unknown = (basis = "진행률 산정 정보 부족") => ({ state: "unknown", percent: null, label, basis });
  if (game?.sport === "bs") {
    // Both collectors normalize inning/half together in this label. Independently
    // merged inning/batting_side/outs fields may describe different observations.
    const match = label.match(/(\d+)\s*회\s*(초|말)/);
    const inning = match ? Number(match[1]) : NaN;
    const half = match?.[2];
    if (!Number.isInteger(inning) || inning < 1 || !half) return unknown();
    if (inning > 9) return { state: "live", percent: null, label: `연장 ${inning}회${half}`,
      basis: "정규 9회 경과 · 종료 시점 미정" };
    return result((inning - 1 + (half === "말" ? .5 : 0)) / 9,
      "정규 9회 기준 · 이닝 단위 추정", `${inning}회${half}`);
  }
  if (game?.sport === "sc") {
    if (/연장|승부차기/.test(label) || numeric(live.clock?.period) > 2) {
      return { state: "live", percent: null, label, basis: "정규시간 경과 · 종료 시점 미정" };
    }
    if (live.clock?.phase === "halftime" || /하프타임|전반 종료|전반종료/.test(label)) {
      return result(.5, "정규 90분 기준 · 추가시간 제외", "하프타임");
    }
    const minute = numeric(live.clock?.elapsed_minute);
    if (!Number.isFinite(minute) || minute < 0) return unknown();
    return result(minute / 90, minute >= 90 ? "정규시간 경과 · 종료 미확정" : "정규 90분 기준 · 추가시간 제외");
  }
  if (game?.sport === "bk") {
    if (/연장|\bOT\b/i.test(label)) return { state: "live", percent: null, label,
      basis: "연장 진행 · 종료 시점 미정" };
    const quarter = Number(label.match(/([1-4])\s*(?:쿼터|Q)/i)?.[1]);
    if (quarter) return result((quarter - 1) / 4, "정규 4쿼터 기준 · 쿼터 단위 추정");
  }
  return unknown(game?.sport === "vl" ? "세트 경기 · 종료까지의 비율은 산정하지 않음" : undefined);
}
