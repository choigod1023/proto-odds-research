import assert from "node:assert/strict";
import test from "node:test";
import { hydrateUnpricedGame, priceBucket, repriceGameOdds, repricePriceGame,
  shinProbabilities } from "./live-odds.js";

test("Python 운영식과 같은 Shin 확률을 계산한다", () => {
  const probabilities = shinProbabilities([2.92, 1.26]);
  assert.ok(Math.abs(probabilities[0] - 0.2744074798866407) < 1e-10);
  assert.ok(Math.abs(probabilities[1] - 0.7255925201133593) < 1e-10);
  assert.ok(Math.abs(probabilities.reduce((sum, value) => sum + value, 0) - 1) < 1e-12);
});

test("실시간 배당과 시장확률·최종 판정을 한 revision으로 다시 만든다", () => {
  const game = {
    round: 101,
    추천: { "선택": "원정" },
    decision_snapshot: { schema_version: "decision-snapshot-v2" },
    options: [
      { market: "승패", label: "", "게임번호": "7010", "선택": "홈", "배당": 3.38,
        "시장확률": 0.2278, "모델확률": 0.31 },
      { market: "승패", label: "", "게임번호": "7010", "선택": "원정", "배당": 1.19,
        "시장확률": 0.7722, "모델확률": 0.69 },
    ],
  };
  const repriced = repriceGameOdds(game, { 7010: [2.92, 1.26] }, "2026-08-27T05:02:24Z");
  assert.equal(repriced._liveOddsRecalculated, true);
  assert.equal(repriced._liveOddsRecalculatedAt, "2026-08-27T05:02:24Z");
  assert.equal(repriced.decision_snapshot, null, "이전 가격의 판정 계약을 재사용하지 않는다");
  assert.equal(repriced.options[0]["시장확률"], 0.2744);
  assert.equal(repriced.options[1]["시장확률"], 0.7256);
  assert.equal(repriced.options[1]["최종확률"], 0.7256);
  assert.equal(repriced.options[1]["확률근거"], "shin_market_live");
  assert.equal(repriced.options[1]._live, true);
  assert.equal(game.options[1]["배당"], 1.19, "원본 산출물은 변경하지 않는다");
});

test("기존 판정에 선택지가 없어도 현재 발매 메타데이터로 복구한다", () => {
  const game = {
    round: 102, date: "08.30(일) 18:00", league: "KBO", sport: "bs",
    home: "KIA", away: "SSG", status: "배당대기", no_odds: true,
    options: [], 해설: "배당이 아직 발표되지 않았다.",
  };
  const markets = {
    7100: { game_no: "7100", date: game.date, league: "KBO", sport: "bs",
      home: "KIA", away: "SSG", market: "승패", label: "", n_way: 2,
      odds: [1.55, 2.05] },
    7101: { game_no: "7101", date: game.date, league: "KBO", sport: "bs",
      home: "KIA", away: "SSG", market: "언더오버", label: "U 10.5", n_way: 2,
      odds: [1.70, 1.82] },
  };
  const hydrated = hydrateUnpricedGame(game, markets, "2026-08-30T01:00:00Z");
  assert.equal(hydrated.status, "경기전");
  assert.equal(hydrated.no_odds, false);
  assert.equal(hydrated.options.length, 4);
  assert.deepEqual(hydrated.options.map((option) => option["선택"]),
    ["홈", "원정", "언더", "오버"]);
  assert.equal(hydrated.options[2].line, 10.5);
  assert.equal(hydrated.해설, null, "낡은 배당 미발표 문장을 제거한다");
  assert.equal(hydrated._liveOddsHydrated, true);
});

test("언더오버 기준점만 바뀌어도 최신 라인으로 옵션과 확률을 다시 만든다", () => {
  const game = {
    round: 102, date: "08.31(월) 18:00", league: "KBO", sport: "bs",
    home: "KIA", away: "SSG", status: "경기전",
    decision_snapshot: { schema_version: "decision-snapshot-v2" },
    options: [
      { market: "언더오버", label: "U 10.5", line: 10.5, "게임번호": "7101",
        "선택": "언더", "배당": 1.70, "시장확률": 0.52, "모델확률": 0.61 },
      { market: "언더오버", label: "U 10.5", line: 10.5, "게임번호": "7101",
        "선택": "오버", "배당": 1.82, "시장확률": 0.48, "모델확률": 0.39 },
    ],
  };
  const markets = { 7101: {
    game_no: "7101", date: game.date, league: "KBO", sport: "bs",
    home: "KIA", away: "SSG", market: "언더오버", label: "U 11.5",
    odds: [1.70, 1.82],
  }};

  const revised = repriceGameOdds(game, { 7101: [1.70, 1.82] },
    "2026-08-31T08:00:00Z", markets);

  assert.equal(revised._liveLineChanged, true);
  assert.equal(revised.decision_snapshot, null);
  assert.deepEqual(revised.options.map((option) => option.line), [11.5, 11.5]);
  assert.deepEqual(revised.options.map((option) => option.label), ["U 11.5", "U 11.5"]);
  assert.deepEqual(revised.options.map((option) => option["모델확률"]), [null, null],
    "이전 기준점의 구조 모델 확률을 새 기준점에 재사용하지 않는다");
  assert.equal(revised.options[0]["최종확률"], revised.options[0]["시장확률"]);
});

test("핸디캡 기준점 변경과 새 게임번호 발행도 시장 revision으로 감지한다", () => {
  const game = {
    round: 102, date: "08.31(월) 19:00", home: "서울", away: "울산",
    options: [
      { market: "핸디캡", label: "H -1.0", line: -1, "게임번호": "7200",
        "선택": "핸디홈", "배당": 1.80 },
      { market: "핸디캡", label: "H -1.0", line: -1, "게임번호": "7200",
        "선택": "핸디원정", "배당": 1.72 },
    ],
  };
  const markets = { 7250: {
    game_no: "7250", date: game.date, home: game.home, away: game.away,
    market: "핸디캡", label: "H -0.5", odds: [1.65, 1.89],
  }};

  const revised = repriceGameOdds(game, {}, "2026-08-31T09:00:00Z", markets);

  assert.equal(revised._liveLineChanged, true);
  assert.deepEqual(revised.options.map((option) => option["게임번호"]), ["7250", "7250"]);
  assert.deepEqual(revised.options.map((option) => option.line), [-0.5, -0.5]);
});

test("가격 비교 카드도 확률·환급률·구간을 즉시 다시 계산한다", () => {
  const game = {
    booking_class: "2-way",
    selections: [
      { name: "홈", odds: 3.38, prob: 0.2278, bucket: "3.0–5.0", hist_roi: -0.14, hist_n: 10 },
      { name: "원정", odds: 1.19, prob: 0.7722, bucket: "1.0–1.5", hist_roi: -0.10, hist_n: 20 },
    ],
  };
  const repriced = repricePriceGame(game, [2.92, 1.26], "2026-08-27T05:02:24Z");
  assert.equal(repriced._liveOddsRecalculated, true);
  assert.equal(repriced.payout, 88.02);
  assert.equal(repriced.selections[0].bucket, "2.2–3.0");
  assert.equal(repriced.selections[0].hist_roi, null, "구간이 바뀌면 이전 구간 실측을 숨긴다");
  assert.equal(repriced.selections[1].hist_roi, -0.10);
  assert.match(repriced.comment, /원정 73%/);
  assert.equal(priceBucket(2.92), "2.2–3.0");
});
