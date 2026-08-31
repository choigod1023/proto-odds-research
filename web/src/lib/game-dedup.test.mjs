import assert from "node:assert/strict";
import test from "node:test";
import { deduplicateGameCards } from "./game-dedup.js";

test("같은 실제 경기의 여러 회차 중 배당이 발표된 최신 카드만 남긴다", () => {
  const base = { sport: "bk", league: "남농월예", date: "08.31(월) 19:00",
    home: "한국M", away: "사우디M" };
  const result = deduplicateGameCards([
    { ...base, round: 102, status: "배당대기", options: [] },
    { ...base, round: 103, status: "경기전", options: [{ 배당: 1.75 }] },
  ]);
  assert.equal(result.length, 1);
  assert.equal(result[0].round, 103);
});

test("같은 팀의 다른 시작 시각 경기는 별도 경기로 유지한다", () => {
  const base = { sport: "bs", league: "MLB", home: "뉴욕양키", away: "보스레드" };
  assert.equal(deduplicateGameCards([
    { ...base, date: "08.30(일) 02:05", round: 102 },
    { ...base, date: "08.30(일) 08:15", round: 103 },
  ]).length, 2);
});
