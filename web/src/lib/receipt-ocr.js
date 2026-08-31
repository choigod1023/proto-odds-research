const compact = (value) => String(value || "").replace(/\s+/g, "").toLowerCase();

const choiceAliases = (choice) => {
  const value = compact(choice);
  const aliases = new Set([value]);
  if (value === "홈") aliases.add("홈승");
  if (value === "원정") ["원정승", "홈패"].forEach((row) => aliases.add(row));
  if (value === "무") aliases.add("무승부");
  if (value.includes("핸디홈")) ["핸디승", "핸디캡홈"].forEach((row) => aliases.add(row));
  if (value.includes("핸디원정")) ["핸디패", "핸디캡원정"].forEach((row) => aliases.add(row));
  return [...aliases].filter(Boolean).sort((a, b) => b.length - a.length);
};

const numberTokens = (text) => [...String(text || "").matchAll(/\d[\d,]*(?:\.\d+)?/g)]
  .map((match) => ({ raw: match[0], value: Number(match[0].replaceAll(",", "")) }))
  .filter((row) => Number.isFinite(row.value));

const gameNumberPattern = (gameNo) => String(gameNo).split("")
  .map((digit) => `[${digit}${digit === "0" ? "oO" : digit === "1" ? "iIlL" : ""}]`)
  .join("[^0-9a-zA-Z]{0,2}");

export function receiptGameNumbers(text, games = []) {
  const source = String(text || "");
  const known = new Set((games || []).flatMap((game) => (game.options || [])
    .map((option) => String(option?.["게임번호"] || "").trim())).filter(Boolean));
  return [...known].filter((gameNo) => new RegExp(`(^|[^0-9])${gameNumberPattern(gameNo)}(?=[^0-9]|$)`, "i").test(source));
}

export function receiptMatches(text, games = []) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const options = (games || []).flatMap((game) => (game.options || []).map((option) => ({ game, option })));
  const found = [];
  const seen = new Set();
  const matchedGameNumbers = new Set();
  for (let index = 0; index < lines.length; index += 1) {
    const context = lines.slice(Math.max(0, index - 2), index + 5).join(" ");
    const normalized = compact(context);
    const gameNumbers = [...new Set(options.map((candidate) => String(candidate.option?.["게임번호"] || "").trim()))]
      .filter((gameNo) => gameNo && !matchedGameNumbers.has(gameNo)
        && new RegExp(`(^|[^0-9])${gameNumberPattern(gameNo)}(?=[^0-9]|$)`, "i").test(lines[index]));
    for (const gameNo of gameNumbers) {
      const candidates = options.filter((candidate) => String(candidate.option?.["게임번호"] || "").trim() === gameNo);
      const tokens = numberTokens(context).filter((row) => row.raw.replaceAll(",", "") !== gameNo && row.value > 1 && row.value < 100);
      const selected = candidates.map((candidate) => {
        const listedOdds = Number(candidate.option?.["배당"]);
        const oddsToken = tokens.sort((a, b) => Math.abs(a.value - listedOdds) - Math.abs(b.value - listedOdds))[0];
        const hasChoice = choiceAliases(candidate.option?.["선택"]).some((alias) => normalized.includes(alias));
        return { candidate, oddsToken, distance: oddsToken ? Math.abs(oddsToken.value - listedOdds) : Infinity, hasChoice };
      }).filter((row) => row.oddsToken && row.distance <= 0.03)
        .sort((a, b) => a.distance - b.distance || Number(b.hasChoice) - Number(a.hasChoice))[0];
      if (!selected) continue;
      const candidate = selected.candidate;
      const key = `${candidate.game.round}|${gameNo}|${candidate.option.market}|${candidate.option.label || ""}|${candidate.option["선택"]}`;
      if (seen.has(key)) continue;
      seen.add(key);
      matchedGameNumbers.add(gameNo);
      const stakeToken = numberTokens(context).filter((row) => row.value >= 100 && row.value !== Number(gameNo)).sort((a, b) => b.value - a.value)[0];
      found.push({
        key, game: candidate.game, option: candidate.option,
        purchaseOdds: selected.oddsToken.value,
        stake: stakeToken?.value || 10000,
        sourceText: context,
      });
    }
  }
  return found;
}

const labeledNumber = (text, labels) => {
  const source = String(text || "").replace(/\s+/g, " ");
  const match = source.match(new RegExp(`(?:${labels.join("|")})[^0-9]{0,16}([0-9][0-9,.]*)`, "i"));
  return match ? Number(match[1].replaceAll(",", "")) : null;
};

export function receiptTicketSummary(text, matches = []) {
  const calculatedOdds = matches.reduce((value, row) => value * Number(row.purchaseOdds || 1), 1);
  const stake = labeledNumber(text, ["개별투표금액", "총투표금액", "투표금액", "구매금액"])
    || matches.find((row) => Number(row.stake) >= 100)?.stake || 10000;
  const combinedOdds = labeledNumber(text, ["예상배당률", "조합배당", "배당률"])
    || Number(calculatedOdds.toFixed(2));
  const expectedPayout = labeledNumber(text, ["예상적중금액", "예상환급금", "적중금액"])
    || Math.round(stake * combinedOdds);
  return { stake, combinedOdds, expectedPayout, legCount: matches.length };
}
