import test from "node:test";
import assert from "node:assert/strict";
import { createBetRecord, createTicketRecords, estimateLiveProbability, groupBetTickets, settleBet } from "./bet-ledger.js";

const bet = createBetRecord(
  { home: "홈", away: "원정", sport: "sc", date: "08.30(일) 19:00" },
  { market: "승무패", "선택": "홈", "배당": 1.8, "시장확률": .55 },
  { stake: 10000 },
);

test("선택과 구매 정보를 단일 베팅 원장으로 만든다", () => {
  assert.equal(bet.stake, 10000);
  assert.equal(bet.purchaseOdds, 1.8);
  assert.equal(bet.openingProbability, .55);
});

test("축구 홈 픽이 후반에 앞서면 실시간 상황 확률이 상승한다", () => {
  const estimate = estimateLiveProbability(bet, {
    status: "STARTED", home_score: 1, away_score: 0,
    clock: { elapsed_minute: 75 },
  });
  assert.ok(estimate.probability > .55);
});

test("종료된 승패 픽은 최종 점수로 0 또는 1이 된다", () => {
  assert.equal(estimateLiveProbability(bet, {
    status: "RESULT", finished: true, home_score: 0, away_score: 1,
  }).probability, 0);
});

test("언더오버와 핸디캡도 최종 점수로 정산한다", () => {
  const totalBet = { ...bet, selection: { market: "언더오버", label: "U 2.5", choice: "오버" } };
  assert.equal(settleBet(totalBet, { finished: true, home_score: 2, away_score: 1 }), "hit");
  const handicapBet = { ...bet, selection: { market: "핸디캡", label: "H -1.5", choice: "핸디홈" } };
  assert.equal(settleBet(handicapBet, { finished: true, home_score: 3, away_score: 1 }), "hit");
});

test("여러 선택은 공통 투입금을 한 번만 가진 조합 티켓으로 묶인다", () => {
  const rows = createTicketRecords([
    { game: bet.game, option: { market: "승패", "선택": "홈", "배당": 1.43, "시장확률": .65 }, purchaseOdds: 1.43 },
    { game: { ...bet.game, home: "다른홈", away: "다른원정" }, option: { market: "언더오버", label: "U/O 2.5", "선택": "오버", "배당": 1.49, "시장확률": .55 }, purchaseOdds: 1.49 },
  ], { stake: 10000, combinedOdds: 2.2, expectedPayout: 22000 });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].ticket.id, rows[1].ticket.id);
  assert.equal(rows[0].ticket.stake, 10000);
  assert.equal(rows.reduce((sum, row) => sum + row.stake, 0), 0);
  assert.equal(groupBetTickets(rows)[0].bets.length, 2);
});
