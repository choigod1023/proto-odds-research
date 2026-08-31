export const ANONYMOUS_BETS_URL = "https://proto-odds-collector.fly.dev/anonymous-bets";

export function stakeBand(value) {
  const amount = Math.max(0, Number(value) || 0);
  if (amount < 5000) return "under_5000";
  if (amount < 10000) return "5000_9999";
  if (amount < 50000) return "10000_49999";
  if (amount < 100000) return "50000_99999";
  return "100000_plus";
}

export function anonymousTicketPayload(rows, ticket = {}) {
  const legs = (rows || []).filter((row) => row?.option).map((row) => ({
    game_no: String(row.option["게임번호"] || ""),
    sport: String(row.game?.sport || "").slice(0, 12),
    league: String(row.game?.league || "").slice(0, 40),
    market: String(row.option.market || "").slice(0, 30),
    label: String(row.option.label || "").slice(0, 30),
    choice: String(row.option["선택"] || "").slice(0, 30),
    purchase_odds: Number(row.purchaseOdds || row.option["배당"]),
  }));
  return {
    schema_version: 1,
    source: "receipt_ocr",
    round: Number(rows?.[0]?.game?.round) || null,
    combo_size: legs.length,
    combined_odds: Number(ticket.combinedOdds) || null,
    stake_band: stakeBand(ticket.stake),
    legs,
  };
}

export async function submitAnonymousTicket(rows, ticket, fetcher = globalThis.fetch) {
  const payload = anonymousTicketPayload(rows, ticket);
  if (!payload.legs.length || !fetcher) return false;
  const response = await fetcher(ANONYMOUS_BETS_URL, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  return response.ok;
}
