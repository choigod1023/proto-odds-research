import assert from "node:assert/strict";
import test from "node:test";
import { commentaryParts, displayCommentary } from "./commentary.js";

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
  assert.match(out, /언더 확률은 63\.6%/);
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

test("실시간 배당에서 추천 자격을 잃으면 산출 시점 수치를 현재 판정으로 쓰지 않는다", () => {
  const text = "산출 시점의 최종 선택은 한화 승이다. 모델확률은 58.0%, 배당은 1.62다. 최근 성적은 한화가 앞선다.";
  assert.deepEqual(commentaryParts(text, {
    hadRecommendation: true, currentEligible: false, canJudge: true,
  }), {
    verdict: "현재 배당 기준 경기 모델 추천은 제외됐다.",
    rest: "최근 성적은 한화가 앞선다.",
  });
});

test("현재 배당에서도 추천 자격이면 산출 시점 판정을 그대로 구분해 보여준다", () => {
  const text = "산출 시점의 최종 선택은 한화 승이다. 모델확률은 58.0%다. 근거 문장이다.";
  assert.deepEqual(commentaryParts(text, {
    hadRecommendation: true, currentEligible: true, canJudge: true,
  }), {
    verdict: "산출 시점의 최종 선택은 한화 승이다.",
    rest: "모델확률은 58.0%다. 근거 문장이다.",
  });
});
