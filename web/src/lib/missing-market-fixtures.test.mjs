import test from "node:test";
import assert from "node:assert/strict";
import { missingMarketFixtures } from "./missing-market-fixtures.js";
const row = { sport:"vl", league:"KOVO여", home:"GS칼텍스", away:"도로공사", date:"10.31(토) 16:00", market:"승패", n_way:2, game_no:"1527", odds:[1.72,1.76] };
const odds = { generated_at:"2026-10-31T05:04:00Z", markets:{"129": {"1527":row, "1528":{...row,market:"핸디캡"}}} };
test("no pick or price created", () => {
 const [g] = missingMarketFixtures({live:[],past:[]}, odds);
 assert.equal(g.round,"129"); assert.equal(g.sourceRows[0].gameNo,"1527"); assert.equal(g.options,undefined); assert.equal(g.prediction_record,undefined); assert.equal(g.odds,undefined);
});
test("same event in a different round is preserved and cancellation is not inferred as current status", () => {
 const source={...odds,markets:{129:{a:{...row,result:"취소"}},130:{b:{...row,game_no:"2010",status:"경기전",result:null}}}};
 const list=missingMarketFixtures({live:[],past:[]},source);
 assert.deepEqual(list.map(x=>[x.round,x.sourceRows[0].result]),[["129","취소"],["130",null]]);
 assert.equal(list[0].status,undefined); assert.equal(list[1].status,undefined); assert.equal(list[1].sourceRows[0].status,"경기전");
 assert.deepEqual(missingMarketFixtures({live:[{...row,round:129}],past:[]},source).map(x=>x.round),["130"]);
});
test("same round conflicting source rows are not silently discarded", () => {
 const source={...odds,markets:{129:{a:{...row,result:"취소"},b:{...row,game_no:"1529",result:"홈승"}}}};
 assert.deepEqual(missingMarketFixtures({},source)[0].sourceRows,[{gameNo:"1527",status:null,result:"취소"},{gameNo:"1529",status:null,result:"홈승"}]);
});
test("invalid calendar dates and times are rejected", () => {
 for (const date of ["02.30(월) 16:00","02.29(월) 16:00","04.31(금) 16:00","10.31(토) 24:00","10.31(토) 16:60"]) {
  assert.equal(missingMarketFixtures({}, {...odds,generated_at:"2026-03-01T00:00:00Z",markets:{129:{a:{...row,date}}}}).length,0,date);
 }
 assert.equal(missingMarketFixtures({}, {...odds,generated_at:"2028-02-28T00:00:00Z",markets:{129:{a:{...row,date:"02.29(화) 16:00"}}}}).length,1);
});
test("year boundary, year-specific identity, malformed fields and sport", () => {
 const a={generated_at:"2026-12-31T15:10:00Z",markets:{1:{a:{...row,date:"01.01(금) 16:00"},b:{...row,date:"03.01(월) 16:00"}}}};
 assert.equal(missingMarketFixtures({},a)[0].year,2027); assert.equal(missingMarketFixtures({},a).length,1);
 assert.equal(missingMarketFixtures({live:[{...row,year:2025,round:129}],past:[]},odds).length,1);
 assert.equal(missingMarketFixtures({}, {...odds,generated_at:null}).length,0);
 assert.equal(missingMarketFixtures({}, {...odds,markets:{129:{a:{...row,sport:"bk"},b:{...row,league:""}}}}).length,0);
});
