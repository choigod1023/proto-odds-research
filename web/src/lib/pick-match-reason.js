import { scheduledAt } from "./match-status.js";

const numeric = (value) => value !== null && value !== undefined && value !== ""
  && typeof value !== "boolean" && String(value).trim() && Number.isFinite(Number(value))
  && Number(value) >= 0 ? Number(value) : null;
const fmt = (value, digits = 1) => Number(value).toFixed(digits);
const missing = (reason = "양 팀의 비교 가능한 경기 기록이 부족해 이 선택을 뒷받침할 경기 근거를 아직 설명할 수 없습니다.") => ({
  status: "missing", reason, counterReason: "선발·최근 경기 기록을 확인한 뒤 판단해야 합니다.", evidence: [],
});
const record = (form) => {
  const match = String(form?.last10 || "").match(/^(\d+)승\s*(?:(\d+)무\s*)?(\d+)패$/);
  if (!match) return null;
  const w = Number(match[1]), d = Number(match[2] || 0), l = Number(match[3]);
  const n = w + d + l;
  return n > 0 && n <= 10 ? { n, text: `${w}승${d ? ` ${d}무` : ""} ${l}패`, rate: (w + d / 2) / n } : null;
};
const chosenSide = (game, option) => {
  const value = String(option?.선택 ?? option?.sel ?? "").trim();
  if (["승", "홈", game.home].includes(value)) return "home";
  if (["패", "원정", game.away].includes(value)) return "away";
  return null;
};

/** Display-only comparison. Never changes a probability, rank or selection.
 * Only structured fields on the exact aligned game are read, never cached prose.
 * Post-start current context cannot explain a historical pregame decision.
 */
export function pickMatchReason(game, option, now = Date.now()) {
  if (!game || !option || !game.home || !game.away) return missing();
  const kickoff = scheduledAt(game);
  if (kickoff == null || kickoff <= now || game._liveStarted
      || !["경기전", "배당대기"].includes(game.status)) {
    return missing("경기 전 근거가 저장되지 않아 당시 선택을 경기 기록으로 설명할 수 없습니다.");
  }
  const market = String(option.market || "");
  const side = ["승패", "승무패"].includes(market) ? chosenSide(game, option) : null;
  const other = side === "home" ? "away" : "home";
  const form = { home: game.form_home || {}, away: game.form_away || {} };
  const rows = [];
  const add = (kind, text, homeAdvantage = null) => rows.push({
    kind, text, direction: !side || homeAdvantage === null ? "context"
      : (homeAdvantage > 0 ? "home" : homeAdvantage < 0 ? "away" : null) === side ? "support"
        : homeAdvantage === 0 ? "context" : "oppose",
  });
  const hr = record(form.home), ar = record(form.away);
  if (hr && ar) add("recent_record",
    `${game.home} 최근 ${hr.n}경기 ${hr.text}, ${game.away} 최근 ${ar.n}경기 ${ar.text}.`, hr.rate - ar.rate);

  const hs = numeric(form.home.avg_scored), hc = numeric(form.home.avg_conceded);
  const as = numeric(form.away.avg_scored), ac = numeric(form.away.avg_conceded);
  const unit = game.sport === "sc" ? "골" : "점";
  // Volleyball form score units are not declared in this legacy payload.
  if ([hs, hc, as, ac].every((n) => n !== null) && hr && ar && game.sport !== "vl") {
    add("recent_balance", `최근 경기당 ${game.home} ${fmt(hs)}득점·${fmt(hc)}실점, ${game.away} ${fmt(as)}득점·${fmt(ac)}실점으로 기록돼 있습니다.`,
      (hs - hc) - (as - ac));
  }

  // Season/player context is separately refreshed. Explicit future timestamps
  // and post-kickoff refreshes must not be presented as pregame evidence.
  const originalInfo = game.선발 || {};
  const observed = Date.parse(originalInfo.updated_at || "");
  const info = originalInfo.updated_at && (!Number.isFinite(observed) || observed > now || observed >= kickoff)
    ? {} : originalInfo;
  if (game.sport === "bs") {
    const h = info.home_detail, a = info.away_detail;
    const he = numeric(h?.stats?.era), ae = numeric(a?.stats?.era);
    if (h?.name && a?.name && he !== null && ae !== null) {
      const hasPeriod = Boolean(h.stats.period || a.stats.period);
      const samePeriod = h.stats.period && h.stats.period === a.stats.period;
      const sameSeason = h.stats.season != null && h.stats.season === a.stats.season;
      const conflictingSeason = h.stats.season != null && a.stats.season != null && !sameSeason;
      const comparable = !conflictingSeason && (hasPeriod ? samePeriod : sameSeason);
      const scope = (stats) => stats.period || (stats.season ? `${stats.season}시즌` : "집계 기간 미확인");
      add("starter", `선발 자료는 ${game.home} ${h.name} ERA ${fmt(he, 2)}(${scope(h.stats)}), ${game.away} ${a.name} ERA ${fmt(ae, 2)}(${scope(a.stats)})입니다.`,
        comparable ? ae - he : null);
    }
  }
  const ht = info.teams?.home, at = info.teams?.away;
  const seasonRecord = (team) => {
    const w = numeric(team?.wins), l = numeric(team?.losses), d = numeric(team?.draws) ?? 0;
    return w !== null && l !== null && w + l > 0
      ? { text: `${w}승${d ? ` ${d}무` : ""} ${l}패`, rate: w / (w + l + (game.sport === "bs" ? 0 : d)) } : null;
  };
  const hseason = seasonRecord(ht), aseason = seasonRecord(at);
  if (hseason && aseason) add("season_record",
    `시즌 누적 성적은 ${game.home} ${hseason.text}, ${game.away} ${aseason.text}입니다.`, hseason.rate - aseason.rate);

  const support = rows.filter((row) => row.direction === "support");
  const oppose = rows.filter((row) => row.direction === "oppose");
  let reason;
  if (side) {
    if (support.length) {
      const labels = { recent_record: "최근 성적", recent_balance: "최근 득실점", starter: "선발 ERA", season_record: "시즌 승률" };
      const lead = support.slice(0, 2);
      reason = `${lead.map((r) => r.text).join(" ")} ${labels[lead[0].kind]} 비교는 ${game[side]} 쪽 선택을 뒷받침합니다.`;
    } else if (oppose.length) {
      reason = `${oppose.slice(0, 2).map((r) => r.text).join(" ")} 확인된 기록은 오히려 ${game[other]} 쪽에 유리해 ${game[side]} 선택을 경기력 우위로 설명하기 어렵습니다.`;
    } else if (rows.length) {
      reason = `${rows.slice(0, 2).map((r) => r.text).join(" ")} 이 자료만으로 ${game[side]}의 뚜렷한 우위를 확인하기는 어렵습니다.`;
    }
  } else if (market === "언더오버" && hr && ar && hs !== null && as !== null && game.sport !== "vl") {
    const line = numeric(option.line);
    if (line !== null && line > 0) {
      reason = `${game.home} 최근 경기당 ${fmt(hs)}${unit}, ${game.away} ${fmt(as)}${unit} 득점으로 두 평균의 합은 ${fmt(hs + as)}${unit}입니다. 이번 선택은 ${line}${unit} ${option.선택 || option.sel}이며, 이 단순 합은 이번 경기 예상 총득점이나 적중 확률을 뜻하지 않습니다.`;
    }
  }
  if (!reason && rows.length) {
    const condition = [market, option.label || option.market_label, option.선택 || option.sel].filter(Boolean).join(" ");
    reason = `${rows.slice(0, 2).map((r) => r.text).join(" ")} 다만 이 기록만으로 ${condition} 조건의 적중을 뒷받침할 수는 없습니다.`;
  }
  if (!reason) return missing();
  let counterReason = oppose.length ? `${oppose[0].text} 선택한 팀에 반대되는 기록도 함께 봐야 합니다.`
    : "이 비교는 경기 기록 설명이며, 기록 차이가 그대로 이번 경기 결과를 보장하지는 않습니다.";
  if (game.sport === "bs" && (!info.home_detail?.name || !info.away_detail?.name)) {
    counterReason += " 양 팀 선발투수 자료가 모두 확인되지는 않았습니다.";
  }
  return { status: support.length ? oppose.length ? "mixed" : "supporting" : oppose.length ? "opposing" : "context",
    reason, counterReason, evidence: rows };
}
