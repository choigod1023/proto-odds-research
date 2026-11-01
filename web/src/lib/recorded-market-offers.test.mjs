import assert from "node:assert/strict";
import test from "node:test";
import { recordedMarketOffers } from "./recorded-market-offers.js";
import { repriceGameOdds } from "./live-odds.js";
const make = (no, market, label, line, names, prices) => names.map((name, i) => ({게임번호:no, market,label,line,n_way:names.length,선택:name,배당:prices[i]}));
const full=make("1544","승무패","",null,["홈","무","원정"],[2.4,3.35,2.3]);
const handicap=make("1545","핸디캡","H +1.0",1,["핸디홈","핸디무","핸디원정"],[1.48,4,4.45]);
const total=make("1546","언더오버","U 2.5",2.5,["언더","오버"],[2.18,1.45]);
test("complete offers retain their own market, line and reissue",()=>{
 const extra=make("1600","언더오버","U 3.5",3.5,["언더","오버"],[1.8,1.8]);
 const offers=recordedMarketOffers([...full,...handicap,...total,...extra]);
 assert.deepEqual(offers.map(({gameNo,market,label})=>[gameNo,market,label]),[["1544","승무패",""],["1545","핸디캡","H +1.0"],["1546","언더오버","U 2.5"],["1600","언더오버","U 3.5"]]);
 assert.deepEqual(offers[2].selections.map(({name,odds})=>({name,odds})),[{name:"언더",odds:2.18},{name:"오버",odds:1.45}]);assert.equal(offers[1].settlementScope,null);
});
test("incomplete, conflicting, ambiguous prices and identities are withheld",()=>{
 for (const rows of [total.slice(0,1),[total[0],{...total[1],배당:0}],[total[0],{...total[1],선택:"언더"}],[total[0],{...total[1],line:3.5}],[total[0],{...total[1],settlement_scope:"extra_time"}],[...total,...make("1546","언더오버","U 3.5",3.5,["언더","오버"],[1.8,1.8])],total.map(row=>({...row,label:""}))]) assert.deepEqual(recordedMarketOffers(rows),[]);
});
test("explicit settlement scope and period are preserved without mutation",()=>{
 const rows=total.map(row=>({...row,period:"full",settlement_scope:"regulation"})), copy=structuredClone(rows), [offer]=recordedMarketOffers(rows);
 assert.equal(offer.period,"full");assert.equal(offer.settlementScope,"regulation");assert.deepEqual(rows,copy);
});


test("different choices sharing a provided selection_id are withheld, including across offers",()=>{
 const conflict=total.map(row=>({...row,selection_id:"same"}));
 assert.deepEqual(recordedMarketOffers(conflict),[]);
 const second=make("1550","핸디캡","H +1.0",1,["핸디홈","핸디무","핸디원정"],[1.48,4,4.45]);
 const a=total.map((row,i)=>({...row,selection_id:i===0?"shared":"other"}));
 const b=second.map((row,i)=>({...row,selection_id:i===0?"shared":`h${i}`}));
 assert.deepEqual(recordedMarketOffers([...a,...b]),[]);
 const reissue=total.map(row=>({...row,게임번호:"1600",selection_id:row.선택}));
 assert.equal(recordedMarketOffers([...total.map(row=>({...row,selection_id:row.선택})),...reissue]).length,2);
});

test("carried-forward prices do not inherit the artifact generation time as an observation time",()=>{
 const options=total.map((row,i)=>({...row,selection_id:`sel${i}`,offer_id:`off${i}`}));
 const game={options,prediction_status:"recorded_pregame",prediction_revision_id:"dec1",prediction_record:{prediction_snapshot_id:"dec1",selection_id:"sel0",offer_id:"off0",market:"언더오버",label:"U 2.5",selection:"언더",odds:2.18,captured_at:"2026-10-31T00:00:00Z"}};
 let before=recordedMarketOffers(options,game)[0];assert.equal(before.selections[0].provenance.kind,"ledger_pick");assert.equal(before.selections[1].provenance.kind,"unverified");
 // The 00:00 price can be carried into an empty 01:00 collection, whose
 // generated_at is updated even though no new observation was made.
 const carried={generated_at:"2026-10-31T01:00:00Z",odds:{1546:[2.05,1.75]}};
 const changed=repriceGameOdds(game,carried.odds,carried.generated_at);
 assert.equal(changed.prediction_status,"prediction_ledger_required");assert.equal(changed.prediction_record,null);
 const after=recordedMarketOffers(changed.options,changed)[0];
 assert.deepEqual(after.selections[0].provenance,{kind:"overlay_unverified",documentGeneratedAt:"2026-10-31T01:00:00Z"});
 assert.equal(recordedMarketOffers(changed.options,{...changed,_liveOddsRecalculatedAt:null})[0].selections[0].provenance.documentGeneratedAt,null);
 assert.equal(recordedMarketOffers(options,{...game,prediction_revision_id:"other"})[0].selections[0].provenance.kind,"unverified");
 assert.equal(recordedMarketOffers(options,{...game,prediction_record:{...game.prediction_record,odds:9}})[0].selections[0].provenance.kind,"unverified");
});
