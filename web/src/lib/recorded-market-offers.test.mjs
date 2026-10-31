import assert from "node:assert/strict";
import test from "node:test";
import { recordedMarketOffers } from "./recorded-market-offers.js";
const make = (no, market, label, line, names, prices) => names.map((name, i) => ({게임번호:no, market,label,line,n_way:names.length,선택:name,배당:prices[i]}));
const full=make("1544","승무패","",null,["홈","무","원정"],[2.4,3.35,2.3]);
const handicap=make("1545","핸디캡","H +1.0",1,["핸디홈","핸디무","핸디원정"],[1.48,4,4.45]);
const total=make("1546","언더오버","U 2.5",2.5,["언더","오버"],[2.18,1.45]);
test("complete offers retain their own market, line and reissue",()=>{
 const extra=make("1600","언더오버","U 3.5",3.5,["언더","오버"],[1.8,1.8]);
 const offers=recordedMarketOffers([...full,...handicap,...total,...extra]);
 assert.deepEqual(offers.map(({gameNo,market,label})=>[gameNo,market,label]),[["1544","승무패",""],["1545","핸디캡","H +1.0"],["1546","언더오버","U 2.5"],["1600","언더오버","U 3.5"]]);
 assert.deepEqual(offers[2].selections,[{name:"언더",odds:2.18},{name:"오버",odds:1.45}]);assert.equal(offers[1].settlementScope,null);
});
test("incomplete, conflicting, ambiguous prices and identities are withheld",()=>{
 for (const rows of [total.slice(0,1),[total[0],{...total[1],배당:0}],[total[0],{...total[1],선택:"언더"}],[total[0],{...total[1],line:3.5}],[total[0],{...total[1],settlement_scope:"extra_time"}],[...total,...make("1546","언더오버","U 3.5",3.5,["언더","오버"],[1.8,1.8])],total.map(row=>({...row,label:""}))]) assert.deepEqual(recordedMarketOffers(rows),[]);
});
test("explicit settlement scope and period are preserved without mutation",()=>{
 const rows=total.map(row=>({...row,period:"full",settlement_scope:"regulation"})), copy=structuredClone(rows), [offer]=recordedMarketOffers(rows);
 assert.equal(offer.period,"full");assert.equal(offer.settlementScope,"regulation");assert.deepEqual(rows,copy);
});
