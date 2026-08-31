import test from "node:test";
import assert from "node:assert/strict";
import { receiptMatches, receiptTicketSummary } from "./receipt-ocr.js";

const game = {
  round: 103, date: "08.31(월) 18:00", home: "두산", away: "LG",
  options: [
    { "게임번호": "217", market: "승패", label: "", "선택": "홈", "배당": 1.72, "시장확률": .55 },
    { "게임번호": "217", market: "승패", label: "", "선택": "원정", "배당": 2.05, "시장확률": .45 },
  ],
};

test("OCR 텍스트의 게임번호·선택·배당·금액을 현재 발매 선택지와 연결한다", () => {
  const matches = receiptMatches("게임번호 217 홈승 1.72 10,000원", [game]);
  assert.equal(matches.length, 1);
  assert.equal(matches[0].option["선택"], "홈");
  assert.equal(matches[0].purchaseOdds, 1.72);
  assert.equal(matches[0].stake, 10000);
});

test("현재 발매 데이터와 일치하지 않는 OCR 문장은 자동 후보로 만들지 않는다", () => {
  assert.deepEqual(receiptMatches("게임번호 999 원정 2.10 5,000원", [game]), []);
});

test("승패라는 마켓명만으로 홈이나 원정 선택을 추측하지 않는다", () => {
  assert.deepEqual(receiptMatches("프로토 승부식 217 승패 10,000원", [game]), []);
});

test("구매내역의 조합배당·공통 투입금·예상적중금을 티켓 단위로 읽는다", () => {
  const summary = receiptTicketSummary("선택경기수 2경기 예상배당률 2.4배 개별투표금액 10,000원 예상적중금액 24,000원", [
    { purchaseOdds: 1.63 }, { purchaseOdds: 1.43 },
  ]);
  assert.deepEqual(summary, { stake: 10000, combinedOdds: 2.4, expectedPayout: 24000, legCount: 2 });
});
