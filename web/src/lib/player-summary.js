const present = (value) => value !== null && value !== undefined && value !== "";

const baseballRate = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3).replace(/^0/, "") : null;
};

function pitcherRole(stats) {
  if (!stats) return "공식 예고 선발";
  const parts = [];
  if (stats.period) parts.push(stats.period);
  if (stats.record) parts.push(stats.record);
  if (present(stats.era)) parts.push(`ERA ${stats.era}`);
  if (present(stats.whip)) parts.push(`WHIP ${stats.whip}`);
  if (present(stats.strikeouts)) parts.push(`${stats.strikeouts}탈삼진`);
  return parts.join(" · ") || "공식 예고 선발";
}

function hitterRole(player) {
  const stats = player?.stats;
  if (!stats) return `${player.order || "-"}번 · ${player.position || "야수"}`;
  const parts = [];
  const avg = baseballRate(stats.avg);
  const ops = baseballRate(stats.ops);
  if (avg) parts.push(`타율 ${avg}`);
  if (present(stats.home_runs)) parts.push(`${stats.home_runs}홈런`);
  if (present(stats.rbi)) parts.push(`${stats.rbi}타점`);
  if (ops) parts.push(`OPS ${ops}`);
  return `${stats.season ? `${stats.season}시즌 · ` : ""}${parts.join(" · ")}`;
}

function bestHitter(players) {
  return [...(players || [])]
    .filter((player) => player?.name && player.position !== "투수")
    .sort((a, b) => {
      const as = a.stats || {}, bs = b.stats || {};
      return (Number(bs.ops) || 0) - (Number(as.ops) || 0)
        || (Number(bs.home_runs) || 0) - (Number(as.home_runs) || 0)
        || (Number(bs.rbi) || 0) - (Number(as.rbi) || 0)
        || (Number(a.order) || 99) - (Number(b.order) || 99);
    })[0] || null;
}

function genericRole(player, sport) {
  const parts = [];
  if (present(player.games ?? player.apps)) parts.push(`${player.games ?? player.apps}경기`);
  if (sport === "sc") {
    if (present(player.goals)) parts.push(`${player.goals}골`);
    if (present(player.assists)) parts.push(`${player.assists}도움`);
    if (present(player.xg)) parts.push(`xG ${player.xg}`);
  } else if (sport === "bk") {
    if (present(player.points)) parts.push(`평균 ${player.points}점`);
    if (present(player.rebounds)) parts.push(`${player.rebounds}리바운드`);
    if (present(player.assists)) parts.push(`${player.assists}도움`);
  } else if (sport === "vl") {
    if (present(player.points)) parts.push(`${player.points}득점`);
    if (present(player.blocks)) parts.push(`${player.blocks}블로킹`);
    if (present(player.serves)) parts.push(`${player.serves}서브`);
  }
  return parts.join(" · ") || "공식 시즌 선수 기록";
}

export function playerSummaryFor(game) {
  const info = game?.["선발"] || {};
  const players = [];

  if (game?.sport === "bs") {
    for (const side of ["home", "away"]) {
      const starter = info[`${side}_detail`] || (info[side] ? { name: info[side] } : null);
      if (starter?.name) players.push({
        name: starter.name, team: game[side], position: "선발투수",
        role: pitcherRole(starter.stats), profileUrl: starter.profile_url || null,
      });
    }
    for (const side of ["home", "away"]) {
      const hitter = bestHitter(info.lineups?.[side]);
      if (hitter) players.push({
        name: hitter.name, team: game[side],
        position: `${hitter.order || "-"}번 타자${hitter.position ? ` · ${hitter.position}` : ""}`,
        role: hitterRole(hitter), profileUrl: hitter.profile_url || null,
      });
    }
    const status = info.lineup_status || {};
    const state = status.state;
    const officialToday = status.official_today === true || state === "official_today";
    const note = officialToday
      ? "오늘 NPB 공식 선발 타순과 공식 시즌 선수 기록을 반영했다."
      : state === "mixed_official_projected"
        ? "공개된 팀은 오늘 공식 타순, 미공개 팀은 최근 완료 경기 기반 예상 타순이다."
        : info.lineups ? "타자는 최근 공식 경기 기반 예상 타순이며 오늘 확정 명단이 아니다." : "";
    return { players, note };
  }

  const keyPlayers = info.key_players || {};
  for (const side of ["home", "away"]) {
    const player = keyPlayers[side]?.[0];
    if (player?.name) players.push({
      name: player.name, team: game[side], position: player.position || "핵심 선수",
      role: genericRole(player, game?.sport), profileUrl: player.profile_url || null,
    });
  }
  return { players, note: players.length ? "공식 시즌 기록의 팀별 주요 선수를 표시한다." : "" };
}
