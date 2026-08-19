import assert from "node:assert/strict";
import test from "node:test";
import { displayCommentary } from "./commentary.js";

const options = [
  { market: "언더오버", line: 11.5, 선택: "언더", 모델확률: 0.6357 },
  { market: "언더오버", line: 11.5, 선택: "오버", 모델확률: 0.3643 },
];

test("실제 11.5 라인과 충돌하는 레거시 8.5 문장을 교체한다", () => {
  const out = displayCommentary({
    sport: "bs",
    options,
    해설: "LA다저스의 승리를 예상한다. 합계 10.2점으로 기준선인 8.5점을 넘어서는 오버가 예상된다. 원정 팀의 전력이 1.3점 앞서 있어 2점 차 이상으로 갈릴 가능성이 있다.",
  });
  assert.match(out, /실제 언더오버 기준점은 11\.5점/);
  assert.match(out, /언더 63\.6%/);
  assert.doesNotMatch(out, /8\.5/);
  assert.doesNotMatch(out, /2점 차 이상/);
});

test("실제 라인이 8.5인 경기는 건드리지 않는다", () => {
  const text = "실제 기준선인 8.5점보다 낮아 언더로 본다.";
  assert.equal(displayCommentary({
    sport: "bs",
    options: options.map((o) => ({ ...o, line: 8.5 })),
    해설: text,
  }), text);
});

test("이미 실제 11.5를 쓰는 새 해설은 건드리지 않는다", () => {
  const text = "시장 기본값은 LA다저스 승 65%다. 실제 기준선 11.5점과 비교하면 언더 쪽이다.";
  assert.equal(displayCommentary({ sport: "bs", options, 해설: text }), text);
});
