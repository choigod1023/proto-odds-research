import assert from "node:assert/strict";
import test from "node:test";
import { playerSummaryFor } from "./player-summary.js";

const game = {
  sport: "bs", home: "소프트뱅크", away: "라쿠텐",
  선발: {
    home: "마츠모토 하루", away: "후루샤 다쿠미",
    home_detail: { name: "마츠모토 하루", stats: { period: "2026시즌", record: "8승 4패", era: 2.5, whip: .99, strikeouts: 90 } },
    away_detail: { name: "후루샤 다쿠미", stats: { era: 3.1 } },
    lineups: {
      home: [
        { name: "야나기마치 다쓰루", order: 3, position: "좌익수", stats: { season: 2026, avg: .288, home_runs: 9, rbi: 55, ops: .811 } },
        { name: "야마카와 호타카", order: 4, position: "1루수", stats: { season: 2026, avg: .240, home_runs: 25, rbi: 70, ops: .850 } },
      ],
      away: [{ name: "무라바야시 이쓰키", order: 1, position: "유격수", stats: { season: 2026, avg: .275, home_runs: 5, rbi: 40, ops: .720 } }],
    },
    lineup_status: { state: "projected_from_recent_official" },
  },
};

test("NPB 요약에 양 팀 선발투수와 대표 타자를 함께 넣는다", () => {
  const summary = playerSummaryFor(game);
  assert.deepEqual(summary.players.map((player) => player.name), ["마츠모토 하루", "후루샤 다쿠미", "야마카와 호타카", "무라바야시 이쓰키"]);
  assert.match(summary.players[0].role, /8승 4패.*ERA 2.5.*90탈삼진/);
  assert.match(summary.players[2].role, /25홈런.*OPS \.850/);
  assert.match(summary.note, /오늘 확정 명단이 아니다/);
});

test("오늘 공식 타순이면 공식 상태를 명시한다", () => {
  const official = structuredClone(game);
  official.선발.lineup_status = { state: "official_today", official_today: true };
  assert.match(playerSummaryFor(official).note, /오늘 NPB 공식 선발 타순/);
});
