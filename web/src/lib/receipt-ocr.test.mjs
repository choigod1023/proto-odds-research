import test from "node:test";
import assert from "node:assert/strict";
import { mergedReceiptRows, receiptGameNumbers, receiptMatches, receiptRows, receiptTicketSummary } from "./receipt-ocr.js";

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

test("게임번호를 우선 식별하고 선택 글자가 없어도 구매 배당으로 픽을 판별한다", () => {
  const matches = receiptMatches("217 조합 · 한경기\n야구 승패\n승 1.72 패 -", [game]);
  assert.equal(matches.length, 1);
  assert.equal(matches[0].option["선택"], "홈");
});

test("OCR이 게임번호 사이를 띄우거나 1을 I로 읽어도 프로토 번호를 복원한다", () => {
  assert.deepEqual(receiptGameNumbers("게임 I 7 2I7 승 1.72", [game]), ["217"]);
});

test("배당과 선택을 놓쳐도 경기번호를 읽었으면 확인 가능한 폴더 행을 보존한다", () => {
  const rows = receiptRows("217 조합 한경기\n야구 승패\n선택경기수 1경기", [game]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].sourceText, "217");
  assert.equal(rows[0].needsConfirmation, true);
  assert.equal(rows[0].optionChoices.length, 2);
});

test("영수증 하단 사업자번호를 프로토 경기번호로 오인하지 않는다", () => {
  const noisyGame = { ...game, options: game.options.map((option) => ({ ...option, "게임번호": "7287" })) };
  assert.deepEqual(receiptGameNumbers("사업자등록번호 72-87-03278", [noisyGame]), []);
});

test("여러 폴더의 경기번호를 데이터 배열이 아닌 사진에 나온 순서로 유지한다", () => {
  const later = { ...game, options: game.options.map((option) => ({ ...option, "게임번호": "8076" })) };
  assert.deepEqual(receiptGameNumbers("8076 조합\n217 조합", [game, later]), ["8076", "217"]);
});

test("숫자 전용 OCR은 누락 폴더를 보강하고 일반 OCR의 확정 픽은 유지한다", () => {
  const later = { ...game, options: game.options.map((option) => ({ ...option, "게임번호": "8076" })) };
  const rows = mergedReceiptRows("217 조합 한경기 승 1.72", "217\n8076", [game, later]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].option["게임번호"], "217");
  assert.equal(rows[0].option["선택"], "홈");
  assert.equal(rows[1].sourceText, "8076");
  assert.equal(rows[1].needsConfirmation, true);
});

test("경기번호를 놓쳐도 고유한 기준점과 배당 조합으로 폴더를 복원한다", () => {
  const later = { ...game, home: "중국", away: "레바논", options: [
    { "게임번호": "8076", market: "언더오버", label: "U 165.5", line: 165.5, "선택": "언더", "배당": 1.79 },
    { "게임번호": "8076", market: "언더오버", label: "U 165.5", line: 165.5, "선택": "오버", "배당": 1.73 },
  ] };
  const rows = mergedReceiptRows("농구 언더오버 U/0 165.5 - 1.73", "", [game, later]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].option["게임번호"], "8076");
  assert.equal(rows[0].option["선택"], "오버");
});

test("구매내역의 조합배당·공통 투입금·예상적중금을 티켓 단위로 읽는다", () => {
  const summary = receiptTicketSummary("선택경기수 2경기 예상배당률 2.4배 개별투표금액 10,000원 예상적중금액 24,000원", [
    { purchaseOdds: 1.63 }, { purchaseOdds: 1.43 },
  ]);
  assert.deepEqual(summary, { stake: 10000, combinedOdds: 2.4, expectedPayout: 24000, legCount: 2 });
});
