// Display only complete recorded offers; never infer a prediction or live availability.
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

/** Use the recorded market/game number and do not merge reissued lines. */
export function recordedMarketOffers(options) {
  if (!Array.isArray(options)) return [];
  const byNumber = new Map();
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
    const selections = rows.map((row) => row["선택"]);
    if (new Set(selections).size !== expected.length || !expected.every((name) => selections.includes(name))) continue;
    offers.push({ gameNo, market, label, period: period || null, settlementScope: scope || null,
      selections: expected.map((name) => {
        const row = rows.find((item) => item["선택"] === name);
        return { name, odds: Number(row["배당"]) };
      }) });
  }
  return offers.sort((a, b) => a.gameNo.localeCompare(b.gameNo, "en", { numeric: true }));
}
