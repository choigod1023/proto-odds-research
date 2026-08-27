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
  const sport = game?.sport;
  const supported = ["bs", "sc", "bk", "vl"].includes(sport);
  const tabs = [];
  if (supported || commentary || starter || team) {
    tabs.push({ id: "summary", label: "경기 분석" });
  }
  // 선수 명단과 결장·경고·부상은 같은 확인 흐름으로 묶는다. 자료가 0명이어도
  // 프로토 지원 종목은 발표 전과 미연결 상태를 구분해 보여준다.
  if (supported || starter || hasTendency || availability) {
    tabs.push({ id: "players", label: "선수·출전" });
  }
  if (supported || game?.decision_snapshot) {
    tabs.push({ id: "evidence", label: "픽 근거·수식" });
  }
  return tabs;
}

export function pitcherMetrics(starter) {
  const s = starter?.stats;
  if (!s) return [];
  return [
    ["ERA", s.era], ["WHIP", s.whip], ["승-패", s.record],
    ["이닝", s.innings_display ?? s.innings], ["탈삼진", s.strikeouts],
    ["K/9", s.k9], ["BB/9", s.bb9], ["HR/9", s.hr9],
    [s.fip_approx ? "FIP*" : "FIP", s.fip],
    ["평균 이닝", s.avg_ip], ["선발", s.games_started],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");
}
