import assert from "node:assert/strict";
import test from "node:test";
import { infoTabs, pitcherMetrics, starterFor, unavailableFor } from "./game-info.js";

test("상세 선발 구조와 레거시 문자열을 모두 읽는다", () => {
  assert.equal(starterFor({ 선발: { home: "곽빈" } }, "home").name, "곽빈");
  const game = { 선발: { home: "곽빈", home_detail: { name: "곽빈", stats: { era: 2.91 } } } };
  assert.equal(starterFor(game, "home").stats.era, 2.91);
});

test("경기 설명과 판정 자료를 세 구획으로 합친다", () => {
  const game = { sport: "bs", form_home: { last10: "7승 3패" }, 선발: { home: "A" } };
  assert.deepEqual(infoTabs(game, "해설").map((x) => x.id),
    ["summary", "players", "evidence"]);
});

test("축구도 발표 전부터 통합 선수·출전 탭을 숨기지 않는다", () => {
  const game = { sport: "sc", form_home: { last10: "5승 3무 2패" } };
  assert.deepEqual(infoTabs(game, "해설").map((x) => x.id),
    ["summary", "players", "evidence"]);
});

test("농구와 배구도 통합 선수·출전 탭을 항상 연다", () => {
  for (const sport of ["bk", "vl"]) {
    const tabs = infoTabs({ sport }, "해설");
    assert.deepEqual(tabs.map((x) => x.id), ["summary", "players", "evidence"]);
    assert.equal(tabs.find((x) => x.id === "players").label, "선수·출전");
  }
});
test("0도 유효한 투수 지표로 표시하고 부상 배열을 정리한다", () => {
  assert.deepEqual(pitcherMetrics({ stats: { era: 0, fip: null, games_started: 2 } }),
    [["ERA", 0], ["선발", 2]]);
  assert.deepEqual(pitcherMetrics({ stats: {
    era: 2.5, whip: .99, record: "8승 4패", innings_display: "100⅔",
    strikeouts: 90, k9: 8.05,
  } }), [["ERA", 2.5], ["WHIP", .99], ["승-패", "8승 4패"],
    ["이닝", "100⅔"], ["탈삼진", 90], ["K/9", 8.05]]);
  assert.deepEqual(unavailableFor({ 선발: { unavailable: { home: [{ name: "A" }, {}] } } }, "home"),
    [{ name: "A" }]);
});

test("출전 변수의 표준 사유와 예상 영향을 보존한다", () => {
  const row = { name: "A", reason_label: "경고누적", impact_label: "큼", impact_score: .4 };
  assert.deepEqual(unavailableFor({ 선발: { unavailable: { away: [row] } } }, "away"), [row]);
});
