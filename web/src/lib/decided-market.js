const scoreValue = (v) => ["number", "string"].includes(typeof v) && String(v).trim() !== ""
  && Number.isInteger(Number(v)) && Number(v) >= 0;
const pair = (v) => Array.isArray(v) && v.length === 2 && v.every(scoreValue);
const labels = { hit: "적중", miss: "적중실패" };

/** Display inference only. Never a bookmaker settlement or a ledger mutation. */
export function decidedMarket(sport, selection, live, { now = Date.now(), feedAt } = {}) {
  if (!live || live.cancelled || live.postponed || live.stale
      || !["STARTED", "IN_PROGRESS", "LIVE", "RESULT", "END", "ENDED", "FINAL"].includes(live.status)
      || !["sc", "bs", "bk"].includes(sport)) return null;
  const observed = Date.parse(live.observed_at || feedAt || "");
  if (!Number.isFinite(observed) || now - observed > 600000 || observed > now + 5000) return null;
  const market = String(selection?.market || "");
  const half = market.startsWith("전반");
  const family = half ? market.slice(2) : market;
  const choice = String(selection?.selection ?? selection?.choice ?? "").replace(/^전반/, "");
  const label = String(selection?.label || "");
  if (!half && (/전반|후반|쿼터|세트|이닝|회|^h[\s(]/.test(label))) return null;
  let scores;
  if (half) {
    if (live.first_half_complete !== true || !pair(live.first_half_score)) return null;
    scores = live.first_half_score;
  } else {
    if (family !== "언더오버" || live.finished) return null;
    // Never count extra-time/shootout goals toward a regulation-time total.
    if (sport === "sc") {
      if (pair(live.regular_time_score)) scores = live.regular_time_score;
      else {
        const period = live.current_period ?? live.clock?.period;
        if (![1, 2].includes(period)) return null;
      }
    }
    scores ||= [live.home_score, live.away_score];
    if (!pair(scores)) return null;
  }
  const [home, away] = scores.map(Number);
  let hit;
  if (family === "언더오버") {
    const numbers = label.match(/[-+]?\d+(?:\.\d+)?/g);
    if (numbers?.length !== 1 || !["언더", "오버"].includes(choice)) return null;
    const line = Number(numbers[0]);
    // Quarter lines require split-stake grading; equality needs official push rules.
    if (line < 0 || !Number.isInteger(line * 2) || home + away === line) return null;
    if (!half && home + away < line) return null;
    hit = choice === "오버" ? home + away > line : home + away < line;
  } else if (half && ["승패", "승무패", "핸디캡"].includes(family)) {
    let diff = home - away;
    let side = choice;
    if (family === "핸디캡") {
      const numbers = label.match(/[-+]?\d+(?:\.\d+)?/g);
      if (numbers?.length !== 1 || !Number.isInteger(Number(numbers[0]) * 2)) return null;
      diff += Number(numbers[0]);
      side = choice.replace(/^핸디/, "");
      if (diff === 0 && selection.n_way !== 3 && side !== "무") return null;
    }
    if (!["홈", "원정", "무"].includes(side) || (family === "승패" && (diff === 0 || side === "무"))) return null;
    hit = side === "홈" ? diff > 0 : side === "원정" ? diff < 0 : diff === 0;
  } else return null;
  const state = hit ? "hit" : "miss";
  return { state, label: labels[state], source: "live_condition", inPlay: true,
    note: `${half ? `전반 종료 점수 ${home}:${away}` : `현재 합계 ${home + away}점·기준점 돌파`} 기준 · 공식 정산 전 (정정·취소 시 변경 가능)` };
}
