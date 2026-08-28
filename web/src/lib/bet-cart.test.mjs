import test from "node:test";
import assert from "node:assert/strict";
import { cartOdds, cartPayout, toggleCartSelection } from "./bet-cart.js";

test("조합 배당과 예상 환급금을 계산한다", () => {
  const items = [{ odds: 1.5 }, { odds: 2 }];
  assert.equal(cartOdds(items), 3);
  assert.deepEqual(cartPayout(items, 10_000), { combined: 3, gross: 30_000, profit: 20_000 });
});

test("같은 경기의 다른 선택은 기존 선택을 교체한다", () => {
  const old = { id: "1-home", gameId: "1", selection: "홈" };
  const next = { id: "1-away", gameId: "1", selection: "원정" };
  assert.deepEqual(toggleCartSelection([old], next), [next]);
});

test("같은 선택을 다시 누르면 장바구니에서 제거한다", () => {
  const item = { id: "1-home", gameId: "1" };
  assert.deepEqual(toggleCartSelection([item], item), []);
});
