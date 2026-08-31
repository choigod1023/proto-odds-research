import test from "node:test";
import assert from "node:assert/strict";
import { anonymousTicketPayload, stakeBand, submitAnonymousTicket } from "./anonymous-bets.js";

test("익명 통계에는 원본 이미지·구매번호·개인 식별자가 포함되지 않는다", () => {
  const rows = [{ game: { round: 102, sport: "sc", league: "K리그1" }, option: {
    "게임번호": "7830", market: "승무패", label: "", "선택": "원정", "배당": 1.43,
  }, purchaseOdds: 1.43 }];
  const payload = anonymousTicketPayload(rows, { stake: 10000, combinedOdds: 1.43 });
  assert.equal(payload.stake_band, "10000_49999");
  assert.equal(payload.legs[0].game_no, "7830");
  assert.equal(JSON.stringify(payload).includes("purchase_number"), false);
  assert.equal(JSON.stringify(payload).includes("image"), false);
});

test("익명 티켓을 JSON POST로 전송한다", async () => {
  let request;
  const ok = await submitAnonymousTicket([{ game: {}, option: { "게임번호": "1", "선택": "홈", "배당": 2 } }],
    { stake: 5000 }, async (url, options) => { request = { url, options }; return { ok: true }; });
  assert.equal(ok, true);
  assert.equal(request.options.method, "POST");
});

test("투입금은 원금 대신 구간으로만 변환한다", () => {
  assert.equal(stakeBand(4999), "under_5000");
  assert.equal(stakeBand(100000), "100000_plus");
});
