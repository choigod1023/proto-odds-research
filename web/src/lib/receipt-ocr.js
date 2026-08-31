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

export function receiptMatches(text, games = []) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const options = (games || []).flatMap((game) => (game.options || []).map((option) => ({ game, option })));
  const found = [];
  const seen = new Set();
  for (let index = 0; index < lines.length; index += 1) {
    const context = [lines[index - 1], lines[index], lines[index + 1]].filter(Boolean).join(" ");
    const normalized = compact(context);
    for (const candidate of options) {
      const gameNo = String(candidate.option?.["게임번호"] || "").trim();
      if (!gameNo || !new RegExp(`(^|\\D)${gameNo}(?=\\D|$)`).test(context)) continue;
      if (!choiceAliases(candidate.option?.["선택"]).some((alias) => normalized.includes(alias))) continue;
      const key = `${candidate.game.round}|${gameNo}|${candidate.option.market}|${candidate.option.label || ""}|${candidate.option["선택"]}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const tokens = numberTokens(context).filter((row) => row.raw !== gameNo);
      const listedOdds = Number(candidate.option?.["배당"]);
      const oddsToken = tokens.filter((row) => row.value > 1 && row.value < 100)
        .sort((a, b) => Math.abs(a.value - listedOdds) - Math.abs(b.value - listedOdds))[0];
      const stakeToken = tokens.filter((row) => row.value >= 100).sort((a, b) => b.value - a.value)[0];
      found.push({
        key, game: candidate.game, option: candidate.option,
        purchaseOdds: oddsToken?.value || listedOdds,
        stake: stakeToken?.value || 10000,
        sourceText: context,
      });
    }
  }
  return found;
}
