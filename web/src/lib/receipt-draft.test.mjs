import test from "node:test";
import assert from "node:assert/strict";
import {
  receiptDrafts,
  scannedTicketSummary,
  linkReceiptDraft,
  receiptSaveIssue,
  receiptRecordRows,
} from "./receipt-draft.js";
import { recordLive, liveKey } from "./bet-ledger.js";

// Synthetic clubs/numbers; private receipt images and ticket identifiers stay local.
const scan = {
  text: "선택경기수 예상배당률 개별투표금액 예상적중금액\n2경기    2.5배    10.000원    25,000원",
  rows: [
    {
      text: "09.06 (일)\n17:00     북부 구단 vs 남부 구단\n4217 조합 한경기 야구 승패",
      numberText: "09.06\n4217\n17:00 마감",
      buttonText: "패\n1.59",
      purchaseOdds: 1.59,
    },
    {
      text: "09.06 (일)\n동부 구단 vs 서부 구단\n4319 축구 언더오버 uo 25",
      detailText: "축구 언더오버 U/0 2.5",
      numberText: "09.06\n4319\n19:00 마감",
      buttonText: "오버\n1.54",
      purchaseOdds: 1.54,
    },
  ],
};
test("receipt drafts do not depend on current feed, retain printed choice/price/line", () => {
  const rows = receiptDrafts(scan);
  assert.deepEqual(
    rows.map(({ gameNo, choice, purchaseOdds, line, home, away }) => ({
      gameNo,
      choice,
      purchaseOdds,
      line,
      home,
      away,
    })),
    [
      {
        gameNo: "4217",
        choice: "원정",
        purchaseOdds: 1.59,
        line: "",
        home: "북부 구단",
        away: "남부 구단",
      },
      {
        gameNo: "4319",
        choice: "오버",
        purchaseOdds: 1.54,
        line: "2.5",
        home: "동부 구단",
        away: "서부 구단",
      },
    ],
  );
  assert.ok(rows.every((row) => !row.reviewed && !row.game && row.year === ""));
});
test("summary table reads printed 2.5 rather than multiplying the two individual prices", () => {
  assert.deepEqual(scannedTicketSummary(scan), {
    legCount: 2,
    combinedOdds: 2.5,
    stake: 10000,
    expectedPayout: 25000,
    purchasedAt: "",
    reviewed: false,
  });
  assert.equal(
    scannedTicketSummary({ text: "3경기   4.4배   10.000원   44.000원" })
      .expectedPayout,
    44000,
  );
  assert.equal(
    scannedTicketSummary({ text: "2경기   3.5배   10.000원   35.0004" })
      .expectedPayout,
    35000,
  );
});
test("missing summary stays missing, never manufactures stake or payout", () => {
  const result = scannedTicketSummary({
    text: "4217 패 1.59",
    rows: scan.rows,
  });
  assert.equal(result.stake, "");
  assert.equal(result.expectedPayout, "");
  assert.equal(result.legCount, null);
});
test("old compact screenshot U means under and game number is not a date/odds", () => {
  const row = receiptDrafts({
    rows: [
      {
        text: "봄 구단 : 가을 구단\nU/0 182.5",
        numberText: "12.19 (금)\n21\n19:00 마감",
        buttonText: "U 1.82",
        purchaseOdds: 1.82,
      },
    ],
  })[0];
  assert.equal(row.choice, "언더");
  assert.equal(row.gameNo, "21");
  assert.equal(row.md, "12.19");
  assert.equal(row.line, "182.5");
});
test("unknown/ambiguous selected button is not inferred from screen position", () => {
  const row = receiptDrafts({
    rows: [
      {
        text: "9 패 1.91",
        numberText: "9\n21",
        buttonText: "1.91",
        purchaseOdds: 1.91,
      },
    ],
  })[0];
  assert.equal(row.choice, "");
  assert.equal(row.gameNo, "");
});
const row = { ...receiptDrafts(scan)[0], year: "2026" };
const game = {
  date: "09.06(일) 17:00",
  year: 2026,
  round: 105,
  home: "북부 구단",
  away: "남부 구단",
  options: [
    {
      게임번호: "4217",
      market: "승패",
      선택: "원정",
      배당: 1.99,
      시장확률: 0.65,
    },
  ],
};
test("matching requires year/date/both teams/market/number and never replaces purchase odds", () => {
  assert.equal(linkReceiptDraft(row, [game]).purchaseOdds, 1.59);
  assert.equal(linkReceiptDraft(row, [game]).game, game);
  assert.equal(linkReceiptDraft({ ...row, year: "2025" }, [game]).game, null);
  assert.equal(linkReceiptDraft({ ...row, year: "" }, [game]).game, null);
  assert.equal(
    linkReceiptDraft(row, [{ ...game, away: "다른 팀" }]).game,
    null,
  );
  assert.equal(
    linkReceiptDraft(row, [game, { ...game, round: 106 }]).game,
    null,
  );
});
test("saved receipt never imports current opening probability as a past observation", () => {
  const records = receiptRecordRows([linkReceiptDraft(row, [game])]);
  assert.equal(records[0].option["시장확률"], undefined);
  assert.equal(records[0].purchaseOdds, 1.59);
});
test("saving requires original review, date, all fields and the complete ticket", () => {
  const ticket = {
    ...scannedTicketSummary(scan),
    legCount: 1,
    reviewed: true,
    purchasedAt: "2026-09-06",
  };
  assert.ok(receiptSaveIssue([row], ticket));
  assert.equal(receiptSaveIssue([{ ...row, reviewed: true }], ticket), "");
  assert.ok(
    receiptSaveIssue([{ ...row, reviewed: true }], {
      ...ticket,
      purchasedAt: "",
    }),
  );
  assert.ok(
    receiptSaveIssue([{ ...row, reviewed: true }], { ...ticket, legCount: 2 }),
  );
  assert.ok(receiptSaveIssue([{ ...row, reviewed: true, choice: "" }], ticket));
  assert.ok(
    receiptSaveIssue([{ ...row, reviewed: true, md: "02.30" }], ticket),
  );
  assert.ok(
    receiptSaveIssue([{ ...row, reviewed: true, choice: "언더" }], ticket),
  );
});

test("historical receipt never picks up another year's live result with same teams and MM.DD", () => {
  const past = { ...game, year: 2025 };
  const live = { date: "2026-09-06", finished: true };
  const index = new Map([[liveKey(past), live]]);
  assert.equal(recordLive(index, past), undefined);
  assert.equal(recordLive(index, game), live);
});
