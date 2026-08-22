/** 경기 설명 패널의 자료형을 한곳에서 정리한다. 레거시 선발 문자열도 계속 읽는다. */
export function starterFor(game, side) {
  const starters = game?.["선발"];
  if (!starters) return null;
  const detail = starters[`${side}_detail`];
  if (detail) return detail;
  const legacy = starters[side];
  return legacy ? { name: legacy, announced: true } : null;
}

export function teamRecordFor(game, side) {
  return game?.["선발"]?.teams?.[side] || null;
}

export function unavailableFor(game, side) {
  const rows = game?.["선발"]?.unavailable?.[side];
  return Array.isArray(rows) ? rows.filter((x) => x?.name) : [];
}

export function sourceFor(game) {
  const s = game?.["선발"];
  if (!s?.source) return null;
  return { name: s.source, url: s.source_url || null, updatedAt: s.updated_at || null };
}

export function infoTabs(game, commentary = "") {
  const starter = starterFor(game, "home") || starterFor(game, "away");
  const team = game?.form_home || game?.form_away || game?.["h2h"]
    || teamRecordFor(game, "home") || teamRecordFor(game, "away");
  const tendency = game?.["라인업"] || {};
  const hasTendency = !!(tendency.home || tendency.away);
  const availability = unavailableFor(game, "home").length
    || unavailableFor(game, "away").length || hasTendency || game?.["선발"]?.lineups;
  const tabs = [];
  if (commentary) tabs.push({ id: "summary", label: "요약" });
  if (game?.sport === "bs" || starter || hasTendency) tabs.push({ id: "players", label: "선발·라인업" });
  if (team) tabs.push({ id: "teams", label: "팀 흐름" });
  // 자료가 0명이어도 야구는 '현재 공식 부상 명단 없음/미연결'을 구분해 보여준다.
  if (game?.sport === "bs" || availability) tabs.push({ id: "availability", label: "부상·출전" });
  return tabs;
}

export function pitcherMetrics(starter) {
  const s = starter?.stats;
  if (!s) return [];
  return [
    ["ERA", s.era], [s.fip_approx ? "FIP*" : "FIP", s.fip], ["WHIP", s.whip],
    ["K/9", s.k9], ["BB/9", s.bb9], ["HR/9", s.hr9],
    ["평균 이닝", s.avg_ip], ["선발", s.games_started],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");
}
