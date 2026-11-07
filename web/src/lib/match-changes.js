import { scheduledAt } from "./match-status.js";
import { marketOutcomeNames } from "./recorded-market-offers.js";

export const CHANGE_FILTERS = [
  ["all", "변경된 경기"],
  ["odds", "배당 변경"],
  ["line", "기준점 변경"],
  ["lineup", "공식 선발 확인"],
  ["settled", "공식 정산 완료"],
];

const HOUR = 3600000;
const timestamp = (value) => typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
  ? Date.parse(value) : NaN;
const timeFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
export const matchChangeTime = (time) => timeFormat.format(time);

function fixtureKey(round, game) {
  return JSON.stringify([String(round), game.date, game.home, game.away]);
}

export function indexMarketHistory(liveOdds) {
  const index = new Map();
  for (const [round, markets] of Object.entries(liveOdds?.markets || {})) {
    const history = liveOdds?.history?.[round];
    if (!history) continue;
    for (const row of Object.values(markets || {})) {
      const gameNo = String(row?.game_no || "");
      const entries = history[gameNo];
      if (!Array.isArray(entries) || !row.date || !row.home || !row.away) continue;
      const key = fixtureKey(round, row);
      if (!index.has(key)) index.set(key, []);
      index.get(key).push({ row, entries, gameNo });
    }
  }
  return index;
}

export function matchMarketHistory(index, game) {
  const start = scheduledAt(game);
  return (index.get(fixtureKey(game.round, game)) || []).flatMap(({ row, entries, gameNo }) =>
    (row.sport && game.sport && row.sport !== game.sport) ||
    (row.league && game.league && row.league !== game.league) ? [] : entries
      .filter(entry => start == null || Math.abs(timestamp(entry?.observed_at) - start) < 90 * 24 * HOUR)
      .map(entry => ({ ...entry, gameNo })));
}

export function marketHistoryGroups(rows = []) {
  const groups = new Map();
  for (const row of rows) {
    const time = timestamp(row?.observed_at);
    const odds = row?.odds;
    const names = marketOutcomeNames(row?.market, odds?.length);
    if (!Number.isFinite(time) || !/^\d+$/.test(row?.gameNo ?? "") || !names
      || !Array.isArray(odds) || !odds.every(value => Number.isFinite(value) && value > 1)) continue;
    const line = Number.isFinite(row.line) ? row.line : null;
    if (/^(?:전반)?(?:핸디캡|언더오버)$/.test(row.market)) {
      const labelLine = String(row.label || "").match(/[-+]?\d+(?:\.\d+)?/);
      if (line == null || !labelLine || Number(labelLine[0]) !== line) continue;
    }
    const key = `${row.gameNo}|${row.market}`;
    if (!groups.has(key)) groups.set(key, { key, market: row.market, gameNo: row.gameNo, points: [] });
    groups.get(key).points.push({ time, line, label: row.label || "", odds, names });
  }
  return [...groups.values()].map(group => {
    const points = group.points.sort((a, b) => a.time - b.time);
    const segments = [];
    for (const point of points) {
      let segment = segments.at(-1);
      if (!segment || segment.line !== point.line || segment.names.length !== point.names.length) {
        segment = { line: point.line, label: point.label, names: point.names, points: [] };
        segments.push(segment);
      }
      segment.points.push(point);
    }
    return { ...group, segments };
  });
}

const terminal = (result) => ["hit", "miss", "void"].includes(result?.result);

export function matchTimelineEvents(game, now = Date.now()) {
  const start = scheduledAt(game);
  const lineup = game.baseball_current?.lineup_status || game["선발"]?.lineup_status;
  const lineupTime = timestamp(lineup?.observed_at);
  const records = [game.prediction_record, game.market_prediction_record, game.featured_pick_record];
  const kickoff = Number.isFinite(start) ? start : null;
  const events = [];
  if ((lineup?.official_today === true || lineup?.state === "announced") && lineupTime <= now) {
    events.push({ kind: "lineup", time: lineupTime, label: "공식 선발 명단 확인" });
  }
  const prediction = records[0] || records[1] || records[2];
  const captured = timestamp(prediction?.captured_at);
  if (kickoff != null && captured <= now && captured <= kickoff - 30 * 60000) {
    events.push({ kind: "pick", time: captured, label: "사전 픽 저장" });
  }
  if (kickoff != null) events.push({ kind: "kickoff", time: kickoff, label: "예정 시작 시각" });
  const settled = records.flatMap(record => {
    const results = record?.market_recommendation_results;
    const rows = Array.isArray(results?.items) ? results.items : Object.values(results || {});
    const time = timestamp(record?.settled_at);
    return time <= now && (kickoff == null || time >= kickoff)
      && (terminal(record) || terminal(record?.result) || rows.some(terminal)) ? [time] : [];
  });
  if (settled.length) events.push({ kind: "settled", time: Math.max(...settled), label: "공식 정산 기록" });
  return events.sort((a, b) => a.time - b.time);
}

export function recentMatchChanges(game, now) {
  const events = matchTimelineEvents(game, now).filter(event => ["lineup", "settled"].includes(event.kind));
  for (const { key, market, points } of marketHistoryGroups(game._marketHistory)) {
    for (let i = 1; i < points.length; i++) {
      const previous = points[i - 1], current = points[i];
      if (previous.time >= current.time || points[i - 2]?.time === previous.time
        || points[i + 1]?.time === current.time || previous.names.length !== current.names.length) continue;
      if (previous.line !== current.line && previous.line != null && current.line != null) {
        events.push({ kind: "line", product: key, time: current.time, label: `${market} ${previous.line} → ${current.line}` });
      } else if (previous.line === current.line) {
        const side = current.odds.findIndex((value, index) => value !== previous.odds[index]);
        if (side === -1) continue;
        const label = `${market} ${current.names[side]} ${previous.odds[side].toFixed(2)} → ${current.odds[side].toFixed(2)}배`;
        events.push({ kind: "odds", product: key, side, time: current.time, label });
      }
    }
  }
  const latest = new Map();
  for (const event of events) {
    if (event.time <= now && event.time > now - 24 * HOUR
      && (!latest.has(event.kind) || latest.get(event.kind).time < event.time)) latest.set(event.kind, event);
  }
  return [...latest.values()].sort((a, b) => b.time - a.time);
}
