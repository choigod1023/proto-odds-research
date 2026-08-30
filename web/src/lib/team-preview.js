const number = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const rate = (value) => {
  const parsed = number(value);
  return parsed === null ? null : parsed.toFixed(3).replace(/^0/, "");
};

export function playerFeature(player, sport) {
  if (!player?.name) return null;
  const stats = player.stats || player;
  const facts = [];
  let characteristic = "주요 전력";
  if (sport === "bs") {
    if (number(stats.era) !== null) facts.push(`ERA ${Number(stats.era).toFixed(2)}`);
    if (number(stats.whip) !== null) facts.push(`WHIP ${Number(stats.whip).toFixed(2)}`);
    if (number(stats.ops) !== null) facts.push(`OPS ${rate(stats.ops)}`);
    if (number(stats.home_runs) !== null) facts.push(`${stats.home_runs}홈런`);
    characteristic = number(stats.era) !== null ? "선발 운영의 중심" : "타선 생산력의 중심";
  } else if (sport === "sc") {
    if (number(stats.goals) !== null) facts.push(`${stats.goals}골`);
    if (number(stats.assists) !== null) facts.push(`${stats.assists}도움`);
    if (number(stats.xg) !== null) facts.push(`xG ${stats.xg}`);
    const goals = number(stats.goals) || 0, assists = number(stats.assists) || 0;
    characteristic = goals > assists ? "득점 마무리의 중심"
      : assists > goals ? "기회 창출의 중심" : "공격 전개의 중심";
  } else if (sport === "bk") {
    if (number(stats.points) !== null) facts.push(`${stats.points}점`);
    if (number(stats.rebounds) !== null) facts.push(`${stats.rebounds}리바운드`);
    if (number(stats.assists) !== null) facts.push(`${stats.assists}도움`);
    characteristic = (number(stats.assists) || 0) >= (number(stats.rebounds) || 0)
      ? "볼 배급과 공격 조율" : "득점·리바운드 기여";
  } else {
    if (number(stats.points) !== null) facts.push(`${stats.points}득점`);
    if (number(stats.blocks) !== null) facts.push(`${stats.blocks}블로킹`);
    if (number(stats.serves) !== null) facts.push(`${stats.serves}서브`);
    characteristic = (number(stats.blocks) || 0) > 0 ? "공격과 네트 수비의 중심" : "공격 득점의 중심";
  }
  return { name: player.name, position: player.position || null, characteristic,
    facts, status: player.status || null };
}

function derivedTraits(record, form) {
  const traits = [];
  const scored = number(record?.goals_per_game ?? record?.points_per_game ?? form?.avg_scored);
  const conceded = number(record?.conceded_per_game ?? form?.avg_conceded);
  if (scored !== null && conceded !== null) {
    if (scored - conceded >= .35) traits.push("공격 생산력이 실점 억제보다 두드러진 팀");
    else if (conceded - scored >= .35) traits.push("공수 균형 회복이 필요한 팀");
    else traits.push("득점과 실점 흐름이 비교적 균형적인 팀");
  }
  if (form?.trend === "상승") traits.push("최근 경기 내용이 상승세");
  if (form?.trend === "하락") traits.push("최근 경기 내용이 하락세");
  if (form?.streak) traits.push(String(form.streak));
  return traits;
}

export function teamPreviewsFor(game) {
  const info = game?.["선발"] || {};
  const profiles = info.team_profiles || {};
  return ["home", "away"].map((side) => {
    const profile = profiles[side] || {};
    const record = profile.record || info.teams?.[side] || {};
    const form = side === "home" ? game?.form_home : game?.form_away;
    let rawPlayers = profile.key_players || info.key_players?.[side] || [];
    if (game?.sport === "bs") {
      const starter = info[`${side}_detail`] || (info[side] ? { name: info[side] } : null);
      const lineup = info.lineups?.[side] || [];
      rawPlayers = [starter, ...lineup].filter(Boolean);
    }
    const players = rawPlayers.map((player) => playerFeature(player, game?.sport)).filter(Boolean).slice(0, 3);
    const characteristics = [...new Set([
      ...(profile.characteristics || []), ...derivedTraits(record, form),
    ])].slice(0, 5);
    const unavailable = (profile.unavailable || info.unavailable?.[side] || [])
      .filter((player) => player?.name).slice(0, 3);
    return { side, team: game?.[side], characteristics, players, unavailable };
  });
}

export function teamPreviewSentence(previews) {
  return (previews || []).map((preview) => {
    const trait = preview.characteristics.slice(0, 2).join(" · ");
    const players = preview.players.slice(0, 2)
      .map((player) => `${player.name}(${player.characteristic}${player.facts.length ? ` · ${player.facts.join(" · ")}` : ""})`)
      .join(", ");
    return [preview.team, trait, players].filter(Boolean).join(" — ");
  }).filter((text) => text.split(" — ").length > 1).join(" / ");
}
