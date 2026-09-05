const LABELS = { hit: "적중", miss: "적중실패", void: "무효" };
const finalOutcome = (state, record, source) => ({ state, label: LABELS[state], record, source });
const validScore = (value) => ["string", "number"].includes(typeof value)
  && String(value).trim() !== "" && Number.isInteger(Number(value)) && Number(value) >= 0;

/** Evaluate the saved selection, never choose a new pick from postgame prices. */
export function recommendationOutcome(game, live = game?._liveState) {
  const record = game?.prediction_record;
  if (!record) return { state: "unrecorded", label: "사전 예측 기록 없음", record: null };
  if (Object.hasOwn(LABELS, record.result)) return finalOutcome(record.result, record, "ledger");
  const selected = record.selection_id && game.options?.find((option) =>
    option.selection_id === record.selection_id);
  if (typeof selected?.적중 === "boolean") {
    return finalOutcome(selected.적중 ? "hit" : "miss", record, "official");
  }

  // A market result is usable only for the same game, market and original line.
  const rows = Object.values(game?._officialMarkets || {}).filter((row) =>
    row.home === game.home && row.away === game.away && row.date === game.date
    && row.market === record.market && (row.label || "") === (record.label || "")
    && (!selected?.게임번호 || String(row.game_no) === String(selected.게임번호)));
  if (rows.length === 1) {
    const row = rows[0];
    if (["취소", "연기", "중단", "무효"].includes(row.result)) {
      return finalOutcome("void", record, "official");
    }
    const names = {
      "승패": ["홈", "원정"], "승무패": ["홈", "무", "원정"],
      "핸디캡": row.n_way === 3 ? ["핸디홈", "핸디무", "핸디원정"] : ["핸디홈", "핸디원정"],
      "언더오버": ["언더", "오버"], "승①패": ["홈2+", "1점차", "원정2+"],
      "승⑤패": ["홈6+", "5점차이내", "원정6+"], "홀짝": ["홀", "짝"],
    }[record.market];
    const winners = row.n_way === 3
      ? { 홈승: 0, 핸디승: 0, 무승부: 1, 핸디무: 1, "①": 1, "⑤": 1, 홈패: 2, 핸디패: 2 }
      : { 홈승: 0, 핸디승: 0, 언더: 0, 홀: 0, 홈패: 1, 핸디패: 1, 오버: 1, 짝: 1 };
    if (names?.length === row.n_way && names.includes(record.selection)
        && Object.hasOwn(winners, row.result)) {
      return finalOutcome(names[winners[row.result]] === record.selection ? "hit" : "miss", record, "official");
    }
  }

  const pending = { state: "pending", label: "정산 결과 확인 중", record };
  // Cancellation/postponement is not necessarily the bookmaker's void decision.
  if (live?.cancelled || live?.postponed) return pending;
  let score = game.status === "정산" ? game.score : null;
  if (!score && live?.finished === true) {
    // Soccer bets use regulation time; a generic final score may include ET/penalties.
    score = game.sport === "sc" ? live.regular_time_score : [live.home_score, live.away_score];
  }
  if (!Array.isArray(score) || score.length !== 2 || !score.every(validScore)) return pending;
  if (!["bs", "bk", "sc"].includes(game.sport)) return pending;
  const [home, away] = score.map(Number);
  const choice = record.selection;
  const diff = home - away;
  const lineText = String(record.label || "").match(/[-+]?\d+(?:\.\d+)?/);
  const line = lineText ? Number(lineText[0]) : null;
  let hit;
  switch (record.market) {
    case "승패":
      if (!["홈", "원정"].includes(choice)) return pending;
      if (!diff) return pending; // draw treatment needs the official market result
      hit = choice === "홈" ? diff > 0 : diff < 0;
      break;
    case "승무패":
      if (!["홈", "무", "원정"].includes(choice)) return pending;
      hit = choice === "홈" ? diff > 0 : choice === "원정" ? diff < 0 : diff === 0;
      break;
    case "언더오버":
      if (line === null || !["언더", "오버"].includes(choice)) return pending;
      if (home + away === line) return finalOutcome("void", record, "score");
      hit = choice === "언더" ? home + away < line : home + away > line;
      break;
    case "핸디캡": {
      if (line === null || !["핸디홈", "핸디무", "핸디원정"].includes(choice)) return pending;
      const peers = (game.options || []).filter((o) => o.market === record.market
        && (o.label || "") === record.label);
      const threeWay = selected?.n_way === 3 || choice === "핸디무"
        || peers.some((o) => o.선택 === "핸디무");
      if (diff + line === 0 && !threeWay) {
        if (selected?.n_way !== 2) return pending;
        return finalOutcome("void", record, "score");
      }
      hit = choice === "핸디홈" ? diff + line > 0
        : choice === "핸디원정" ? diff + line < 0 : diff + line === 0;
      break;
    }
    default:
      // Partial-game and special markets require their own official outcome.
      return pending;
  }
  return finalOutcome(hit ? "hit" : "miss", record, "score");
}
