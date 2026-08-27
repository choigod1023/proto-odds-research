const TOLERANCE = 1e-12;
const MAX_ITERATIONS = 200;

const multiplicative = (odds) => {
  const implied = odds.map((value) => 1 / Number(value));
  const total = implied.reduce((sum, value) => sum + value, 0);
  return implied.map((value) => value / total);
};

/** Python src/devig.py와 같은 Shin(1993) 마진 제거 계산. */
export function shinProbabilities(odds) {
  const values = (odds || []).map(Number);
  if (values.length < 2 || values.some((value) => !Number.isFinite(value) || value <= 1)) {
    return [];
  }
  const implied = values.map((value) => 1 / value);
  const total = implied.reduce((sum, value) => sum + value, 0);
  if (!(total > 1)) return multiplicative(values);

  const probabilities = (z) => {
    if (z <= TOLERANCE) return implied.map((value) => value / total);
    return implied.map((value) => {
      const root = Math.sqrt(z * z + 4 * (1 - z) * value * value / total);
      return (root - z) / (2 * (1 - z));
    });
  };

  let low = 0;
  let high = 0.9;
  for (let iteration = 0; iteration < MAX_ITERATIONS; iteration += 1) {
    const middle = (low + high) / 2;
    const sum = probabilities(middle).reduce((acc, value) => acc + value, 0);
    if (Math.abs(sum - 1) < TOLERANCE) {
      low = middle;
      high = middle;
      break;
    }
    if (sum > 1) low = middle;
    else high = middle;
  }
  const result = probabilities((low + high) / 2);
  const sum = result.reduce((acc, value) => acc + value, 0);
  const normalized = result.map((value) => value / sum);
  return normalized.every((value) => value > 0 && value < 1)
    ? normalized : multiplicative(values);
}

export function oddsBin(odds) {
  const value = Number(odds);
  const bins = [
    [1, 1.3, "1.0-1.3"], [1.3, 1.5, "1.3-1.5"],
    [1.5, 1.8, "1.5-1.8"], [1.8, 2.2, "1.8-2.2"],
    [2.2, 3, "2.2-3.0"], [3, 5, "3.0-5.0"], [5, Infinity, "5.0+"],
  ];
  return bins.find(([low, high]) => value >= low && value < high)?.[2] || null;
}

export function priceBucket(odds) {
  const value = Number(odds);
  const bins = [
    [1, 1.5, "1.0–1.5"], [1.5, 1.8, "1.5–1.8"],
    [1.8, 2.2, "1.8–2.2"], [2.2, 3, "2.2–3.0"],
    [3, 5, "3.0–5.0"], [5, Infinity, "5.0+"],
  ];
  return bins.find(([low, high]) => value >= low && value < high)?.[2] || "5.0+";
}

const rounded = (value, digits = 4) => Number(Number(value).toFixed(digits));

/**
 * 실시간 배당 벡터마다 시장확률·최종확률·shadow 진단값을 같은 시점으로 다시 만든다.
 * 운영식은 검증된 잔차가 없는 Shin 시장 기준이므로 브라우저에서도 결정적으로 재현된다.
 */
export function repriceGameOdds(game, roundOdds, generatedAt = null) {
  if (!roundOdds || !game?.options?.length) return game;
  const groups = new Map();
  game.options.forEach((option, index) => {
    const gameNumber = String(option?.["게임번호"] ?? "");
    if (!groups.has(gameNumber)) groups.set(gameNumber, []);
    groups.get(gameNumber).push({ option, index });
  });

  const replacements = new Map();
  let changed = false;
  for (const [gameNumber, rows] of groups) {
    const fresh = roundOdds?.[gameNumber];
    if (!Array.isArray(fresh) || fresh.length < rows.length) continue;
    const nextOdds = rows.map((_, index) => Number(fresh[index]));
    if (nextOdds.some((value) => !Number.isFinite(value) || value <= 1)) continue;
    if (!rows.some(({ option }, index) => Number(option?.["배당"]) !== nextOdds[index])) continue;
    const probabilities = shinProbabilities(nextOdds);
    if (probabilities.length !== rows.length) continue;
    changed = true;
    const overround = nextOdds.reduce((sum, value) => sum + 1 / value, 0);
    rows.forEach(({ option, index }, groupIndex) => {
      const market = rounded(probabilities[groupIndex]);
      const modelRaw = option?.["모델확률"];
      const model = modelRaw == null || modelRaw === "" ? Number.NaN : Number(modelRaw);
      const next = {
        ...option,
        "배당": nextOdds[groupIndex],
        "시장확률": market,
        "최종확률": market,
        "확률근거": "shin_market_live",
        "AI반영": false,
        "AI잔차": Number.isFinite(model) ? rounded(model - market) : null,
        "괴리": Number.isFinite(model) ? rounded(Math.abs(model - market)) : null,
        "예상손익": Number.isFinite(model) ? rounded(model * nextOdds[groupIndex] - 1) : null,
        _live: true,
        _liveOverround: rounded(overround),
      };
      delete next["제외"];
      delete next["추천점수"];
      delete next["선택근거"];
      replacements.set(index, next);
    });
  }
  if (!changed) return game;
  return {
    ...game,
    options: game.options.map((option, index) => replacements.get(index) || option),
    추천: null,
    decision_snapshot: null,
    _liveOddsRecalculated: true,
    _liveOddsRecalculatedAt: generatedAt,
  };
}

export function repricePriceGame(game, fresh, generatedAt = null) {
  if (!Array.isArray(fresh) || fresh.length < (game?.selections || []).length) return game;
  const nextOdds = game.selections.map((_, index) => Number(fresh[index]));
  if (nextOdds.some((value) => !Number.isFinite(value) || value <= 1)) return game;
  if (!game.selections.some((selection, index) => Number(selection.odds) !== nextOdds[index])) {
    return game;
  }
  const probabilities = shinProbabilities(nextOdds);
  if (probabilities.length !== game.selections.length) return game;
  const overround = nextOdds.reduce((sum, value) => sum + 1 / value, 0);
  const payout = rounded(100 / overround, 2);
  const selections = game.selections.map((selection, index) => {
    const bucket = priceBucket(nextOdds[index]);
    return {
      ...selection,
      odds: nextOdds[index],
      prob: rounded(probabilities[index]),
      bucket,
      hist_roi: bucket === selection.bucket ? selection.hist_roi : null,
      hist_n: bucket === selection.bucket ? selection.hist_n : null,
      _live: true,
    };
  });
  const favorite = [...selections].sort((a, b) => b.prob - a.prob)[0];
  return {
    ...game,
    selections,
    overround: rounded(overround),
    payout,
    comment: `실시간 배당으로 다시 계산했습니다. 시장은 ${favorite.name} ` +
      `${Math.round(favorite.prob * 100)}%로 봅니다(배당 ${favorite.odds.toFixed(2)}). ` +
      `현재 환급률은 ${payout.toFixed(1)}%입니다.`,
    _liveOddsRecalculated: true,
    _liveOddsRecalculatedAt: generatedAt,
  };
}
