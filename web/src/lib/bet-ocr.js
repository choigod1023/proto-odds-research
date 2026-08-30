const number = (value) => {
  const parsed = Number(String(value ?? "").replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
};

const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();

export function parseBetSlipText(text, baseConfidence = null) {
  const source = String(text || "").normalize("NFKC");
  const round = source.match(/(?:제\s*)?(\d{1,3})\s*회차/)?.[1] || null;
  const purchasedAt = source.match(/(20\d{2}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?)/)?.[1] || null;
  const stake = number(source.match(/(?:구매|투입|베팅|결제)?\s*금액\s*[:：]?\s*([\d,]+)\s*원?/)?.[1]);
  const rows = [];
  const candidates = source.split(/\r?\n/).map(clean).filter(Boolean);
  for (const line of candidates) {
    const gameNo = line.match(/(?:경기|게임|G)?\s*0*(\d{1,4})\s*(?:번|호)?/)?.[1];
    const odds = number(line.match(/(?:배당\s*[:：]?\s*)?(\d{1,2}[.][0-9]{1,3})\s*(?:배)?/)?.[1]);
    if (!gameNo || !odds || odds <= 1) continue;
    const market = /언더|오버/.test(line) ? "언더오버"
      : /핸디/.test(line) ? "핸디캡" : /승무패/.test(line) ? "승무패" : "승패";
    const choice = line.match(/(핸디홈|핸디원정|홈승|원정승|언더|오버|무승부|무|승|패)/)?.[1] || "";
    rows.push({ gameNo, market, choice, purchaseOdds: odds, raw: line });
  }
  // OCR이 한 줄을 깨뜨린 경우 전체 텍스트에서 최소 한 건을 복구한다.
  if (!rows.length) {
    const gameNo = source.match(/(?:경기|게임|G)\s*0*(\d{1,4})/)?.[1];
    const odds = number(source.match(/(?:배당\s*[:：]?\s*)(\d{1,2}[.][0-9]{1,3})/)?.[1]);
    if (gameNo && odds > 1) rows.push({ gameNo, market: "승패", choice: "", purchaseOdds: odds, raw: clean(source) });
  }
  const confidence = Number.isFinite(baseConfidence) ? Math.max(0, Math.min(1, baseConfidence / 100)) : null;
  return { round, purchasedAt, stake, rows, confidence, rawText: source };
}

export function flattenOffers(document) {
  return (document?.games || []).flatMap((game) => (game.options || []).map((option) => ({ game, option })));
}

export function matchRecognizedRows(parsed, document) {
  const offers = flattenOffers(document);
  return parsed.rows.map((row, index) => {
    const candidates = offers.filter(({ game, option }) => {
      const sameRound = !parsed.round || String(game.round || "") === String(parsed.round);
      const sameNo = String(option["게임번호"] || "").replace(/^0+/, "") === String(row.gameNo).replace(/^0+/, "");
      return sameRound && sameNo;
    });
    const narrowed = candidates.filter(({ option }) => {
      const sameMarket = !row.market || String(option.market || "") === row.market;
      const sameChoice = !row.choice || String(option["선택"] || "").includes(row.choice);
      return sameMarket && sameChoice;
    });
    const matches = narrowed.length ? narrowed : candidates;
    return {
      ...row, id: `${index}-${row.gameNo}`,
      confidence: parsed.confidence,
      match: matches.length === 1 ? matches[0] : null,
      matchCount: matches.length,
      failureReason: matches.length === 0 ? "현재 배당표에서 회차·게임번호를 찾지 못했습니다."
        : matches.length > 1 ? "선택지가 여러 개입니다. 마켓과 선택을 확인하세요." : null,
    };
  });
}

export function betFingerprint(record) {
  return [record?.game?.round, record?.selection?.gameNo, record?.selection?.market,
    record?.selection?.choice, record?.purchaseOdds, record?.stake].join("|");
}
