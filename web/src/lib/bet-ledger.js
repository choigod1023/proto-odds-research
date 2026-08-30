export const BET_LEDGER_KEY = "proodd-single-bet-ledger-v1";

const clamp = (value, lo = .01, hi = .99) => Math.min(hi, Math.max(lo, value));
const logit = (p) => Math.log(clamp(p) / (1 - clamp(p)));
const logistic = (x) => 1 / (1 + Math.exp(-x));

export function readBetLedger(storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(BET_LEDGER_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function writeBetLedger(bets, storage = globalThis.localStorage) {
  storage?.setItem(BET_LEDGER_KEY, JSON.stringify(bets));
  globalThis.dispatchEvent?.(new CustomEvent("proodd:bet-ledger"));
  return bets;
}

export function createBetRecord(game, option, { stake, purchaseOdds } = {}) {
  const now = new Date().toISOString();
  const probability = Number(option?.["시장확률"]);
  const price = Number(purchaseOdds ?? option?.["배당"]);
  return {
    id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
    createdAt: now,
    game: {
      round: game?.round, date: game?.date, year: game?.year,
      sport: game?.sport, league: game?.league, home: game?.home, away: game?.away,
    },
    selection: {
      gameNo: option?.["게임번호"] || null, market: option?.market,
      label: option?.label || "", choice: option?.["선택"],
    },
    purchaseOdds: Number.isFinite(price) ? price : null,
    stake: Math.max(0, Number(stake) || 0),
    openingProbability: Number.isFinite(probability) ? probability : null,
    history: Number.isFinite(probability) ? [{ at: now, probability, phase: "pregame" }] : [],
  };
}

export function upsertBet(record, storage = globalThis.localStorage) {
  const bets = readBetLedger(storage);
  return writeBetLedger([record, ...bets.filter((bet) => bet.id !== record.id)], storage);
}

export function removeBet(id, storage = globalThis.localStorage) {
  return writeBetLedger(readBetLedger(storage).filter((bet) => bet.id !== id), storage);
}

export function liveKey(game) {
  return `${game?.home}|${game?.away}|${String(game?.date || "").slice(0, 5)}`;
}

function selectedSide(bet) {
  const choice = String(bet?.selection?.choice || "");
  if (/무/.test(choice) && !/승무패/.test(choice)) return "draw";
  if (/원정|패$/.test(choice)) return "away";
  if (/홈|승$/.test(choice)) return "home";
  return null;
}

function progressOf(live, sport) {
  const elapsed = Number(live?.clock?.elapsed_minute);
  if (sport === "sc" && Number.isFinite(elapsed)) return clamp(elapsed / 90, 0, 1);
  const status = String(live?.status_text || "");
  if (sport === "bs") {
    const inning = Number(status.match(/(\d+)회/)?.[1]);
    if (inning) return clamp((inning - .5) / 9, 0, 1);
  }
  return live?.finished ? 1 : live?.status === "STARTED" ? .5 : 0;
}

export function settleBet(bet, live) {
  if (!live?.finished) return live?.cancelled ? "void" : null;
  const home = Number(live.home_score), away = Number(live.away_score);
  if (!Number.isFinite(home) || !Number.isFinite(away)) return null;
  const market = String(bet?.selection?.market || "");
  const choice = String(bet?.selection?.choice || "");
  const line = Number(String(bet?.selection?.label || "").match(/(-?\d+(?:\.\d+)?)/)?.[1]);
  if (/언더오버/.test(market) && Number.isFinite(line)) {
    if (home + away === line) return "void";
    return (/언더/.test(choice) ? home + away < line : home + away > line) ? "hit" : "miss";
  }
  if (/핸디캡/.test(market) && Number.isFinite(line)) {
    const adjustedHome = home + line;
    if (adjustedHome === away) return "void";
    return (/홈|승/.test(choice) ? adjustedHome > away : adjustedHome < away) ? "hit" : "miss";
  }
  if (market === "승①패") {
    const diff = home - away;
    const hit = /1점/.test(choice) ? Math.abs(diff) === 1
      : /홈|승/.test(choice) ? diff >= 2 : diff <= -2;
    return hit ? "hit" : "miss";
  }
  const side = selectedSide(bet);
  if (side === "home") return home > away ? "hit" : "miss";
  if (side === "away") return away > home ? "hit" : "miss";
  if (side === "draw") return home === away ? "hit" : "miss";
  return null;
}

/** 검증 모델이 아니라 구매 당시 확률을 점수와 남은 시간으로 이동시킨 상황 추정치. */
export function estimateLiveProbability(bet, live) {
  const opening = Number(bet?.openingProbability);
  if (!(opening > 0 && opening < 1)) return { probability: null, basis: "missing_opening" };
  if (!live || live.status === "BEFORE") return { probability: opening, basis: "pregame", progress: 0 };
  if (live.cancelled || live.postponed) return { probability: opening, basis: "void", progress: 0 };

  const home = Number(live.home_score);
  const away = Number(live.away_score);
  if (!Number.isFinite(home) || !Number.isFinite(away)) {
    return { probability: opening, basis: "live_score_missing", progress: progressOf(live, bet.game.sport) };
  }
  const progress = progressOf(live, bet.game.sport);
  const choice = String(bet.selection.choice || "");
  const market = String(bet.selection.market || "");
  const total = home + away;
  const side = selectedSide(bet);
  let probability = opening;

  if (/언더오버/.test(market)) {
    const line = Number(String(bet.selection.label || "").match(/(-?\d+(?:\.\d+)?)/)?.[1]);
    if (Number.isFinite(line)) {
      const expectedFinal = progress > .08 ? total / progress : total;
      const signed = /언더/.test(choice) ? line - expectedFinal : expectedFinal - line;
      probability = logistic(logit(opening) + signed * (.55 + progress * 1.2));
    }
  } else if (side) {
    const signedScore = side === "home" ? home - away : side === "away" ? away - home : -Math.abs(home - away);
    const sportWeight = { sc: 1.45, bs: .72, bk: .16, vl: .7 }[bet.game.sport] || .55;
    const drawBoost = side === "draw" && home === away ? 1.2 * progress : 0;
    probability = logistic(logit(opening) + signedScore * sportWeight * (1 + progress * 1.8) + drawBoost);
  }

  if (live.finished) {
    const result = settleBet(bet, live);
    if (result === "hit") probability = 1;
    else if (result === "miss") probability = 0;
    else if (result === "void") probability = opening;
  }
  return {
    probability: clamp(probability, live.finished ? 0 : .01, live.finished ? 1 : .99),
    basis: live.finished ? "final_score" : "score_time_estimate",
    progress,
  };
}

export function appendProbabilityHistory(bet, estimate, live, now = new Date().toISOString()) {
  if (!Number.isFinite(estimate?.probability)) return bet;
  const history = [...(bet.history || [])];
  const previous = history.at(-1);
  if (previous && Math.abs(previous.probability - estimate.probability) < .005
      && previous.score === `${live?.home_score}:${live?.away_score}`) return bet;
  history.push({
    at: now, probability: estimate.probability, phase: estimate.basis,
    score: live ? `${live.home_score}:${live.away_score}` : null,
    status: live?.status_text || null,
  });
  return { ...bet, history: history.slice(-120) };
}
