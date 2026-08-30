import test from "node:test";
import assert from "node:assert/strict";
import { compactTeamPlayerLine, playerFeature, teamPreviewsFor, teamPreviewSentence } from "./team-preview.js";

test("팀별 특징과 핵심 선수를 홈·원정으로 분리한다", () => {
  const game = { sport: "sc", home: "안산", away: "대구",
    form_home: { trend: "하락", avg_scored: 1, avg_conceded: 1.9 },
    선발: { team_profiles: {
      home: { characteristics: ["현재 15위"], key_players: [{ name: "마촙", goals: 4, assists: 4 }] },
      away: { characteristics: ["현재 4위"], key_players: [{ name: "세라핌", goals: 7, assists: 8 }] },
    } } };
  const previews = teamPreviewsFor(game);
  assert.equal(previews[0].team, "안산");
  assert.equal(previews[0].players[0].name, "마촙");
  assert.equal(previews[1].players[0].name, "세라핌");
  assert.match(teamPreviewSentence(previews), /안산.*마촙/);
  assert.match(teamPreviewSentence(previews), /대구.*세라핌/);
  assert.equal(compactTeamPlayerLine(previews), "안산: 마촙 / 대구: 세라핌");
});

test("선수 기록으로 역할 특징을 설명한다", () => {
  assert.equal(playerFeature({ name: "도움왕", goals: 2, assists: 9 }, "sc").characteristic, "기회 창출의 중심");
  assert.match(playerFeature({ name: "투수", stats: { era: 2.31, whip: 1.02 } }, "bs").facts.join(" "), /ERA 2.31/);
});
