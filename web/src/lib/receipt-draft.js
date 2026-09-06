import { buttonChoiceIndex } from "./receipt-image.js";

const compact = (value) =>
  String(value || "")
    .normalize("NFKC")
    .replace(/[^\p{L}\p{N}]/gu, "")
    .toLowerCase();
const choices = ["홈", "무", "원정", "언더", "오버"];
export const RECEIPT_CHOICES = choices;

export function receiptDrafts(scan) {
  return (scan.rows || []).map((row, index) => {
    const text = `${row.text}\n${row.detailText}`;
    const isolated = [
      ...String(row.numberText || "").matchAll(/^\s*(\d{1,4})\s*$/gm),
    ].map((match) => match[1]);
    const gameNo = new Set(isolated).size === 1 ? isolated[0] : "";
    const date = (String(row.numberText || "") + "\n" + text).match(
      /\b(0?[1-9]|1[0-2])[.]\s*(0?[1-9]|[12]\d|3[01])\b/,
    );
    const choiceIndex = buttonChoiceIndex(
      row.buttonText,
      choices.map((choice) => ({ 선택: choice })),
    );
    const choice = choices[choiceIndex] || "";
    const market =
      /언더오버|[Uu4][\/][O0o]/.test(text) || /언더|오버/.test(choice)
        ? "언더오버"
        : /핸디/.test(text)
          ? "핸디캡"
          : /승무패/.test(text)
            ? "승무패"
            : /승패/.test(text)
              ? "승패"
              : /일반|KBL/.test(text)
                ? "승패"
                : "";
    let line =
      market === "언더오버"
        ? text.match(/(?:[Uu4w][\/]\s*[O0o]|uo)\s*([+-]?\d+(?:\.\d+)?)/i)?.[1]
        : market === "핸디캡"
          ? text.match(
              /(?:\bH\s*|핸디[^\d+\-\n]{0,8})([+-]?\d+(?:\.\d+)?)/i,
            )?.[1]
          : "";
    if (market === "언더오버") {
      const observed = [
        ...text.matchAll(/(?:[Uu4w][\/]\s*[O0o]|uo)\s*([+-]?\d+(?:\.\d+)?)/gi),
      ].map((match) => match[1]);
      const decimal = observed.find((value) => value.includes("."));
      if (
        decimal &&
        observed.every(
          (value) => value === decimal || value === decimal.replace(".", ""),
        )
      )
        line = decimal;
      else if (new Set(observed).size > 1) line = "";
      const decimals = [
        ...String(row.lineText || "").matchAll(
          /(?:^|[^\d])(\d+\.\d+)(?=\D|$)/g,
        ),
      ].map((match) => match[1]);
      const cropped =
        decimals.length === 1
          ? decimals[0]
          : String(row.lineText || "").match(
              /(?:[Uu4w][\/]\s*[O0o]|uo)\s*([+-]?\d+(?:\.\d+)?)/i,
            )?.[1];
      if (cropped) line = cropped;
    }
    // Prefer a readable complete row; targeted crops remain evidence, never a dictionary guess.
    const teamLines = [
      ...String(row.text).split(/\n/),
      String(row.teamText),
      ...String(row.detailText).split(/\n/),
    ];
    const teamCandidates = teamLines
      .filter(
        (value) =>
          /[가-힣A-Za-z]{2,}.*(?:\s[vV][sS]\s|\s[:*＊=~«»<>xX]+\s).*?[가-힣A-Za-z]{2,}/.test(
            value,
          ) && !/원정팀|홈팀|대상경기/.test(value),
      )
      .map((value) => value.trim().split(/\s+(?:[vV][sS]|[:*＊=~«»<>xX]+)\s+/))
      .map((parts) => [
        parts[0].split(/\s{2,}/).at(-1),
        parts[1]?.split(/\s{2,}/)[0],
      ])
      .sort(
        (a, b) =>
          (b.join("").match(/[가-힣]/g)?.length || 0) -
          (a.join("").match(/[가-힣]/g)?.length || 0),
      );
    const teamParts = teamCandidates[0] || [row.teamText || "", ""];
    const cleanTeam = (value) => String(value || "").trim();
    return {
      key: `receipt-${index}`,
      selected: true,
      reviewed: false,
      gameNo,
      md: date ? `${date[1].padStart(2, "0")}.${date[2].padStart(2, "0")}` : "",
      year: "",
      home: cleanTeam(teamParts[0]),
      away: cleanTeam(teamParts[1]),
      market,
      line: line ?? "",
      choice,
      purchaseOdds: row.purchaseOdds || "",
      sourceText: text,
      buttonText: row.buttonText,
      teamText: row.teamText,
      game: null,
      option: null,
      matchStatus: "사진 인식값 · 원본 확인 필요",
    };
  });
}

export function linkReceiptDraft(row, games = []) {
  const evidence = compact(`${row.sourceText} ${row.teamText}`);
  const candidates = games.filter((game) => {
    if (!row.md || !String(game.date).startsWith(row.md)) return false;
    if (!row.year || Number(game.year) !== Number(row.year)) return false;
    if (
      !game.home ||
      !game.away ||
      !evidence.includes(compact(game.home)) ||
      !evidence.includes(compact(game.away))
    )
      return false;
    return (game.options || []).some(
      (option) => String(option["게임번호"]) === row.gameNo,
    );
  });
  if (candidates.length !== 1) return { ...row, game: null, option: null };
  const game = candidates[0];
  const options = game.options.filter(
    (option) =>
      String(option["게임번호"]) === row.gameNo &&
      option.market === row.market &&
      buttonChoiceIndex(row.choice, [option]) === 0 &&
      (!/핸디|언더오버/.test(row.market) ||
        (row.line !== "" && Number(option.line) === Number(row.line))),
  );
  if (options.length !== 1) return { ...row, game: null, option: null };
  return {
    ...row,
    home: game.home,
    away: game.away,
    game,
    option: options[0],
    matchStatus: "날짜·팀·마켓·번호 연결됨 · 구매 배당은 사진 값 유지",
  };
}

const moneyToken = (value) => {
  const text = String(value || "").trim();
  const thousands = text.match(/\d{1,3}(?:[,.]\d{3})+/)?.[0];
  if (thousands) return Number(thousands.replace(/[,.]/g, ""));
  const integer = text.match(/^\d{3,}(?=\D|$)/)?.[0];
  return integer ? Number(integer) : null;
};

export function scannedTicketSummary(scan) {
  const lines = `${scan.text}\n${scan.summaryText || ""}`.split(/\n/);
  const candidates = lines.flatMap((line) => {
    const match = line.match(
      /(\d{1,2})\s*경기\s+(\d{1,3}(?:[.,]\d{1,2})?)\s*배\s+(.+)/,
    );
    if (!match) return [];
    const amounts = match[3]
      .trim()
      .split(/\s{2,}/)
      .map(moneyToken)
      .filter((value) => value != null);
    return amounts.length >= 2
      ? [
          {
            legCount: Number(match[1]),
            combinedOdds: Number(match[2].replace(",", ".")),
            stake: amounts[0],
            expectedPayout: amounts[1],
          },
        ]
      : [];
  });
  // No fabricated defaults or replacement with the product of today's prices.
  const result = candidates[0] || {
    legCount: null,
    combinedOdds: "",
    stake: "",
    expectedPayout: "",
  };
  return { ...result, purchasedAt: "", reviewed: false };
}

export function receiptSaveIssue(rows, ticket) {
  const selected = rows.filter((row) => row.selected);
  if (!selected.length) return "저장할 경기를 선택해 주세요.";
  if (!ticket.reviewed || selected.some((row) => !row.reviewed))
    return "원본과 경기·선택·배당·금액을 대조한 뒤 확인을 표시해 주세요.";
  const validDate = (value) =>
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 10) === value;
  if (!validDate(ticket.purchasedAt))
    return "구매일을 입력해 주세요. 연도가 없는 사진은 임의로 올해 기록에 연결하지 않습니다.";
  if (
    selected.some(
      (row) =>
        !validDate(`${row.year}-${row.md.replace(".", "-")}`) ||
        !row.home.trim() ||
        !row.away.trim() ||
        !/^\d{1,4}$/.test(row.gameNo) ||
        !row.market ||
        !row.choice ||
        !Number.isFinite(Number(row.purchaseOdds)) ||
        !(Number(row.purchaseOdds) > 1) ||
        (/언더오버|핸디/.test(row.market) &&
          (row.line === "" || !Number.isFinite(Number(row.line)))),
    )
  )
    return "누락된 경기 날짜·팀·번호·마켓·선택·배당·기준점을 확인해 주세요.";
  if (
    selected.some((row) =>
      row.market === "언더오버"
        ? !["언더", "오버"].includes(row.choice)
        : !["홈", "무", "원정"].includes(row.choice) ||
          (row.market === "승패" && row.choice === "무"),
    )
  )
    return "마켓에 맞는 선택인지 확인해 주세요.";
  if (
    !(Number(ticket.stake) > 0) ||
    !(Number(ticket.combinedOdds) > 1) ||
    !(Number(ticket.expectedPayout) >= 0) ||
    ticket.expectedPayout === ""
  )
    return "티켓의 투입금·조합배당·예상적중금을 확인해 주세요.";
  if (ticket.legCount && selected.length !== Number(ticket.legCount))
    return "사진의 선택경기수와 저장할 경기수가 다릅니다. 누락된 선택이 없는지 확인해 주세요.";
  return "";
}

export function receiptRecordRows(rows) {
  return rows
    .filter((row) => row.selected)
    .map((row) => ({
      game: {
        round: row.game?.round,
        year: Number(row.year),
        date: row.md,
        home: row.home,
        away: row.away,
        sport: row.game?.sport,
        league: row.game?.league,
      },
      // A receipt is not a historical probability observation. Never copy current probabilities.
      option: {
        게임번호: row.gameNo,
        market: row.market,
        label:
          row.line !== ""
            ? `${row.market === "언더오버" ? "U/O" : "H"} ${row.line}`
            : "",
        선택: row.choice,
      },
      purchaseOdds: Number(row.purchaseOdds),
    }));
}
