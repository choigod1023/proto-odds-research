import assert from "node:assert/strict";
import test from "node:test";
import { infoTabs, pitcherMetrics, starterFor, unavailableFor } from "./game-info.js";

test("상세 선발 구조와 레거시 문자열을 모두 읽는다", () => {
  assert.equal(starterFor({ 선발: { home: "곽빈" } }, "home").name, "곽빈");
  const game = { 선발: { home: "곽빈", home_detail: { name: "곽빈", stats: { era: 2.91 } } } };
  assert.equal(starterFor(game, "home").stats.era, 2.91);
});

test("경기 설명을 네 구획으로 나눈다", () => {
  const game = { sport: "bs", form_home: { last10: "7승 3패" }, 선발: { home: "A" } };
  assert.deepEqual(infoTabs(game, "해설").map((x) => x.id),
    ["summary", "players", "teams", "availability"]);
});

test("0도 유효한 투수 지표로 표시하고 부상 배열을 정리한다", () => {
  assert.deepEqual(pitcherMetrics({ stats: { era: 0, fip: null, games_started: 2 } }),
    [["ERA", 0], ["선발", 2]]);
  assert.deepEqual(unavailableFor({ 선발: { unavailable: { home: [{ name: "A" }, {}] } } }, "home"),
    [{ name: "A" }]);
});
