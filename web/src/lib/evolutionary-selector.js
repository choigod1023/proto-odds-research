const GENE_NAMES = [
  "confidence", "odds", "overround", "market_gap", "price_distance",
  "three_way", "handicap", "totals", "first_half", "baseball",
  "basketball", "volleyball", "soccer",
];

const finite = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

function sportFlags(candidate) {
  const text = `${candidate?.sport || ""} ${candidate?.league || ""}`.toLowerCase();
  const baseball = Number(["baseball", "야구", "kbo", "mlb", "npb"].some((x) => text.includes(x)));
  const basketball = Number(["basketball", "농구", "nba", "kbl", "wnba"].some((x) => text.includes(x)));
  const volleyball = Number(["volleyball", "배구", "v리그"].some((x) => text.includes(x)));
  return [baseball, basketball, volleyball, Number(!(baseball || basketball || volleyball))];
}

export function evolutionaryFeatures(candidate, constraints) {
  const probability = finite(candidate?.market_prob);
  const odds = finite(candidate?.odds);
  const overround = finite(candidate?.overround);
  const minimum = finite(constraints?.odds_min);
  const maximum = finite(constraints?.odds_max);
  const target = finite(constraints?.target_odds);
  if (!(probability > 0 && probability < 1) || !(odds > 1) || !(overround > 0) ||
      minimum == null || maximum == null || !(target > 1) ||
      odds < minimum || odds >= maximum) return null;
  const market = String(candidate?.market || "");
  const [baseball, basketball, volleyball, soccer] = sportFlags(candidate);
  return [
    Math.log(probability / (1 - probability)),
    Math.log(odds),
    Math.max(0, overround - 1),
    Math.max(0, finite(candidate?.market_gap) || 0),
    Math.abs(Math.log(odds / target)),
    Number(Number(candidate?.n_way || 2) === 3),
    Number(market.includes("핸디")),
    Number(market.includes("언더오버")),
    Number(market.startsWith("전반")),
    baseball, basketball, volleyball, soccer,
  ];
}

export function rankEvolutionaryCandidates(candidates, rule, limit = 3) {
  const genome = rule?.genome;
  const genomes = (rule?.genomes || []).filter((row) => row && typeof row === "object");
  const constraints = rule?.constraints;
  if ((!genome && !genomes.length) || !constraints) return [];
  const eligible = (candidates || []).flatMap((candidate) => {
    const features = evolutionaryFeatures(candidate, constraints);
    if (!features) return [];
    return [{ candidate, features }];
  });
  if (!eligible.length) return [];
  let scores;
  if (genomes.length) {
    const raw = eligible.map(({ features }) => genomes.map((member) =>
      features.reduce((sum, value, index) =>
        sum + value * Number(member[GENE_NAMES[index]] || 0), 0)));
    const means = genomes.map((_, column) =>
      raw.reduce((sum, row) => sum + row[column], 0) / raw.length);
    const scales = genomes.map((_, column) => {
      const variance = raw.reduce((sum, row) => sum + (row[column] - means[column]) ** 2, 0) /
        raw.length;
      const scale = Math.sqrt(variance);
      return scale < 1e-9 ? 1 : scale;
    });
    scores = raw.map((row) => row.reduce((sum, value, column) =>
      sum + (value - means[column]) / scales[column], 0) / genomes.length);
  } else {
    scores = eligible.map(({ features }) => features.reduce((sum, value, index) =>
      sum + value * Number(genome[GENE_NAMES[index]] || 0), 0));
  }
  return eligible.map(({ candidate }, index) => ({
    ...candidate,
    evolution_score: Number(scores[index].toFixed(6)),
  })).sort((a, b) =>
    b.evolution_score - a.evolution_score ||
    Number(b.market_prob || 0) - Number(a.market_prob || 0) ||
    Number(a.overround || 99) - Number(b.overround || 99) ||
    String(a.kickoff_at || a.date || "").localeCompare(String(b.kickoff_at || b.date || ""))
  ).slice(0, Math.max(0, Number(limit) || 0));
}

export function refreshEvolutionarySelector(selector, candidates) {
  if (!selector?.profiles) return selector || null;
  const profiles = Object.fromEntries(Object.entries(selector.profiles).map(([name, profile]) => {
    const rejected = profile?.historical_status === "rejected_in_historical_audit";
    const ranked = rejected ? [] : rankEvolutionaryCandidates(candidates, profile?.rule, 3);
    return [name, {
      ...profile,
      selected: ranked[0] || null,
      alternatives: ranked.slice(1),
    }];
  }));
  return { ...selector, profiles };
}
