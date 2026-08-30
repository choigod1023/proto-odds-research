import test from "node:test";
import assert from "node:assert/strict";
import { betFingerprint, matchRecognizedRows, parseBetSlipText } from "./bet-ocr.js";

test("프로토 OCR 텍스트에서 회차·경기·배당·금액을 추출한다", () => {
  const parsed = parseBetSlipText(`제102회차\n게임 0012 홈승 배당 1.82\n구매 금액 10,000원`, 87);
  assert.equal(parsed.round, "102");
  assert.equal(parsed.stake, 10000);
  assert.equal(parsed.rows[0].gameNo, "12");
  assert.equal(parsed.rows[0].purchaseOdds, 1.82);
  assert.equal(parsed.confidence, .87);
});

test("회차와 게임번호가 같은 현재 발매 선택지 하나만 매칭한다", () => {
  const parsed = parseBetSlipText(`102회차\n12 홈승 배당 1.82`, 90);
  const document = { games: [{ round: 102, home: "홈", away: "원정", options: [
    { "게임번호": "0012", market: "승패", "선택": "홈승", "배당": 1.82 },
    { "게임번호": "0012", market: "승패", "선택": "원정승", "배당": 2.1 },
  ] }] };
  const [row] = matchRecognizedRows(parsed, document);
  assert.equal(row.match.game.home, "홈");
  assert.equal(row.match.option["선택"], "홈승");
  assert.equal(row.failureReason, null);
});

test("동일 베팅 fingerprint는 이미지가 달라도 중복을 감지한다", () => {
  const record = { game: { round: 102 }, selection: { gameNo: "12", market: "승패", choice: "홈승" }, purchaseOdds: 1.82, stake: 10000 };
  assert.equal(betFingerprint(record), betFingerprint(structuredClone(record)));
});
