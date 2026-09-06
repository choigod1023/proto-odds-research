import { repriceGameOdds } from "./live-odds.js";
import { alignTodayRecommendations, dailyHighlightedSelections } from "./unified-recommendation.js";

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

const sameProtoSelection = (left, right, round) => !!left && !!right &&
  String(left.round ?? round) === String(right.round ?? round) &&
  String(left.game_no ?? left["게임번호"] ?? "") === String(right.game_no ?? right["게임번호"] ?? "") &&
  left.market === right.market &&
  String(left.market_label ?? left.label ?? "") === String(right.market_label ?? right.label ?? "") &&
  String(left.sel ?? left["선택"] ?? "") === String(right.sel ?? right["선택"] ?? "");

/** 조합 구성과 무관하게 오늘의 경기별 최종 추천을 형광 표시 대상으로 만든다. */
export function recommendedTodayPicks(today, currentGames = null, now = Date.now()) {
  const aligned = currentGames == null ? today : alignTodayRecommendations(today, currentGames, now);
  return dailyHighlightedSelections(aligned?.candidates || []);
}

export function currentSlipGames(games, liveOdds, now = new Date()) {
  const activeRounds = new Set((liveOdds?.rounds || []).map(String));
  return (games || []).filter((game) =>
    (!activeRounds.size || activeRounds.has(String(game.round))) &&
      isCurrentSlipDate(game.date, now)).map((game) => repriceGameOdds(
      game,
      liveOdds?.odds?.[String(game.round)],
      liveOdds?.generated_at || null,
      liveOdds?.markets?.[String(game.round)],
    ));
}

export function slipRows(games, liveOdds, now = new Date(), todayPicks = null) {
  const groups = new Map();
  currentSlipGames(games, liveOdds, now).forEach((current) => {
    const recommended = current["추천"] || null;
    const isRecommended = (option) => Array.isArray(todayPicks)
      ? todayPicks.some((pick) => sameProtoSelection(pick, option, current.round))
      : !!recommended && (
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
