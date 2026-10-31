// Display complete offers without asserting their ledger status or live availability.
const OUTCOMES = {
  "승패|2": ["홈", "원정"], "승무패|3": ["홈", "무", "원정"],
  "핸디캡|2": ["핸디홈", "핸디원정"], "핸디캡|3": ["핸디홈", "핸디무", "핸디원정"],
  "언더오버|2": ["언더", "오버"], "홀짝|2": ["홀", "짝"],
  "승①패|3": ["홈2+", "1점차", "원정2+"], "승⑤패|3": ["홈6+", "5점차이내", "원정6+"],
  "전반승패|2": ["전반홈", "전반원정"], "전반승무패|3": ["전반홈", "전반무", "전반원정"],
  "전반언더오버|2": ["전반언더", "전반오버"],
  "전반핸디캡|2": ["전반핸디홈", "전반핸디원정"],
  "전반핸디캡|3": ["전반핸디홈", "전반핸디무", "전반핸디원정"],
};
const LINE_MARKETS = new Set(["핸디캡", "언더오버", "전반핸디캡", "전반언더오버"]);
const text = (value) => typeof value === "string" ? value.trim() : "";

// A generated-at timestamp describes the source artifact, not an independently verified receipt.
function knownTimestamp(value) {
  return typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value)) ? value : null;
}

function priceProvenance(row, game) {
  if (row._live === true) return {
    kind: "observed_unrecorded",
    at: knownTimestamp(game?._liveOddsRecalculatedAt),
  };
  const record = game?.prediction_record;
  const recorded = game?.prediction_status === "recorded_pregame"
    && typeof game?.prediction_revision_id === "string" && game.prediction_revision_id
    && record?.prediction_snapshot_id === game.prediction_revision_id
    && text(row.selection_id) && text(row.offer_id)
    && row.selection_id === record?.selection_id && row.offer_id === record?.offer_id
    && row.market === record?.market && text(row.label) === text(record?.label)
    && row["선택"] === record?.selection && Number(row["배당"]) === Number(record?.odds)
    && knownTimestamp(record?.captured_at);
  return recorded ? { kind: "ledger_pick", at: record.captured_at }
    : { kind: "unverified", at: null };
}

/** Group the observed market/game number without merging reissued lines. */
export function recordedMarketOffers(options, game = null) {
  if (!Array.isArray(options)) return [];
  const byNumber = new Map();
  const meaningsById = new Map();
  for (const row of options) {
    if (!row || typeof row !== "object" || row.selection_id == null) continue;
    const id = text(row.selection_id);
    if (!id) continue;
    const meaning = JSON.stringify([text(row.market), text(row.label), row.line ?? null,
      text(row.period), text(row.settlement_scope), text(row["선택"])]);
    if (!meaningsById.has(id)) meaningsById.set(id, new Set());
    meaningsById.get(id).add(meaning);
  }
  for (const row of options) {
    if (!row || typeof row !== "object") continue;
    const no = String(row["게임번호"] ?? "").trim();
    if (!/^\d+$/.test(no)) continue;
    if (!byNumber.has(no)) byNumber.set(no, []);
    byNumber.get(no).push(row);
  }
  const offers = [];
  for (const [gameNo, rows] of byNumber) {
    const first = rows[0];
    const market = text(first.market), label = text(first.label);
    const n = Number(first.n_way);
    const expected = OUTCOMES[`${market}|${n}`];
    if (!expected || rows.length !== expected.length) continue;
    const line = first.line;
    if (LINE_MARKETS.has(market)) {
      const labelNumber = label.match(/[-+]?\d+(?:\.\d+)?/);
      if (!labelNumber || line === null || line === undefined || line === ""
          || !Number.isFinite(Number(line)) || Number(labelNumber[0]) !== Number(line)) continue;
    }
    const scope = text(first.settlement_scope);
    const period = text(first.period);
    const consistent = rows.every((row) => text(row.market) === market && text(row.label) === label
      && Number(row.n_way) === n && (row.line ?? null) === (line ?? null)
      && text(row.settlement_scope) === scope && text(row.period) === period
      && typeof row["선택"] === "string"
      && (typeof row["배당"] === "number" || (typeof row["배당"] === "string" && row["배당"].trim()))
      && Number.isFinite(Number(row["배당"])) && Number(row["배당"]) > 1);
    if (!consistent) continue;
    if (rows.some((row) => row.selection_id != null
      && (!text(row.selection_id) || meaningsById.get(text(row.selection_id))?.size !== 1))) continue;
    const selections = rows.map((row) => row["선택"]);
    if (new Set(selections).size !== expected.length || !expected.every((name) => selections.includes(name))) continue;
    offers.push({ gameNo, market, label, period: period || null, settlementScope: scope || null,
      selections: expected.map((name) => {
        const row = rows.find((item) => item["선택"] === name);
        return { name, odds: Number(row["배당"]), provenance: priceProvenance(row, game) };
      }) });
  }
  return offers.sort((a, b) => a.gameNo.localeCompare(b.gameNo, "en", { numeric: true }));
}
