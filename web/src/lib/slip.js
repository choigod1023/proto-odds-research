import { repriceGameOdds } from "./live-odds.js";

const KST_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
});

export function isCurrentSlipDate(value, now = new Date()) {
  const match = String(value || "").match(/^(\d{2})\.(\d{2})/);
  if (!match) return true;
  const [yearText, monthText, dayText] = KST_DATE.format(now).split("-");
  let year = Number(yearText);
  const month = Number(match[1]);
  const currentMonth = Number(monthText);
  if (month < currentMonth - 6) year += 1;
  if (month > currentMonth + 6) year -= 1;
  const gameKey = year * 10000 + month * 100 + Number(match[2]);
  const todayKey = Number(yearText) * 10000 + currentMonth * 100 + Number(dayText);
  return gameKey >= todayKey;
}

export function slipRows(games, liveOdds, now = new Date()) {
  const groups = new Map();
  const activeRounds = new Set((liveOdds?.rounds || []).map(String));
  (games || []).forEach((game) => {
    if (activeRounds.size && !activeRounds.has(String(game.round))) return;
    if (!isCurrentSlipDate(game.date, now)) return;
    const current = repriceGameOdds(
      game,
      liveOdds?.odds?.[String(game.round)],
      liveOdds?.generated_at || null,
      liveOdds?.markets?.[String(game.round)],
    );
    const recommended = current["추천"] || game["추천"] || null;
    const isRecommended = (option) => !!recommended && (
      (recommended.selection_id && option.selection_id === recommended.selection_id) ||
      (recommended["게임번호"] && String(option["게임번호"]) === String(recommended["게임번호"]) &&
        option.market === recommended.market && (option.label || "") === (recommended.label || "") &&
        option["선택"] === recommended["선택"])
    );
    (current.options || []).forEach((option) => {
      const number = String(option["게임번호"] || "").trim();
      if (!number) return;
      const key = `${current.round}|${number}|${option.market}|${option.label || ""}`;
      if (!groups.has(key)) groups.set(key, {
        key, round: current.round, number, date: current.date,
        home: current.home, away: current.away, market: option.market,
        label: option.label || "", selectionsByName: new Map(),
      });
      groups.get(key).selectionsByName.set(option["선택"], {
        name: option["선택"], value: Number(option["배당"]), live: option._live === true,
        recommended: isRecommended(option),
      });
    });
  });
  return [...groups.values()].map(({ selectionsByName, ...row }) => ({
    ...row, selections: [...selectionsByName.values()],
  })).sort((a, b) => Number(a.round) - Number(b.round) || Number(a.number) - Number(b.number));
}
