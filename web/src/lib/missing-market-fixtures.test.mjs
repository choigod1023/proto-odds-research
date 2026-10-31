import test from "node:test";
import assert from "node:assert/strict";
import { missingMarketFixtures } from "./missing-market-fixtures.js";
const row = { sport:"vl", league:"KOVO여", home:"GS칼텍스", away:"도로공사", date:"10.31(토) 16:00", market:"승패", n_way:2, odds:[1.72,1.76] };
const odds = { generated_at:"2026-10-31T05:04:00Z", markets:{"129": {"1527":row, "1528":{...row,market:"핸디캡"}}} };
test("shows missing fixture once without creating a pick or exposing post-game prices", () => {
  const [g] = missingMarketFixtures({live:[],past:[]}, odds);
  assert.deepEqual(g,{year:2026,round:"129",sport:"vl",league:"KOVO여",home:"GS칼텍스",away:"도로공사",date:"10.31(토) 16:00"});
  assert.equal(g.options,undefined); assert.equal(g.prediction_record,undefined);
});
test("existing event is not duplicated, across rounds", () => {
  assert.equal(missingMarketFixtures({live:[{...row,round:128}],past:[]},odds).length,0);
});
test("does not merge different dates or leagues, and rejects malformed identity", () => {
  const data={live:[{...row,date:"11.01(일) 16:00"}],past:[]};
  assert.equal(missingMarketFixtures(data,odds).length,1);
  assert.equal(missingMarketFixtures({}, {...odds, markets:{"129":{bad:{...row,league:""}}}}).length,0);
  assert.equal(missingMarketFixtures({}, {...odds, generated_at:null}).length,0);
});
test("year inferred at year boundary and remote stale fixture rejected", () => {
  const a={generated_at:"2026-12-31T15:10:00Z",markets:{1:{a:{...row,date:"01.01(금) 16:00"},b:{...row,date:"03.01(월) 16:00"}}}};
  const list=missingMarketFixtures({},a); assert.equal(list.length,1); assert.equal(list[0].year,2027);
});

test("a prior year fixture does not suppress a current fixture", () => {
  assert.equal(missingMarketFixtures({live:[{...row,year:2025}],past:[]}, odds).length,1);
});
test("other sports are not introduced by this volleyball fallback", () => {
  assert.equal(missingMarketFixtures({}, {...odds,markets:{129:{1:{...row,sport:"bk"}}}}).length,0);
});
