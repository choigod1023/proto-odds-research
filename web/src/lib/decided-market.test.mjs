import assert from "node:assert/strict";
import test from "node:test";
import { decidedMarket } from "./decided-market.js";
import { gamePhase, recommendationOutcome } from "./match-status.js";
import { savedLivePrediction } from "./saved-live-prediction.js";
import { estimateLiveProbability, settleBet } from "./bet-ledger.js";
const now = Date.parse("2026-09-06T11:00:00Z");
const live = { status: "STARTED", home_score: 5, away_score: 4, observed_at: new Date(now).toISOString() };
const selection = { market: "언더오버", label: "U 8.5", selection: "오버" };
const evaluate = (s = selection, l = live, sport = "bs") => decidedMarket(sport, s, l, { now });
const game = (record = selection, l = live) => ({sport:"bs",year:2026,date:"09.06(일) 18:00",home:"홈팀",away:"원정팀",
  _liveState:l, prediction_record:{...record,selection_id:"saved",odds:1.8,probability:.6,captured_at:"2026-09-06T08:00:00Z",result:"pending"}});

test("crossed totals label over hit and under miss without finishing game or rewriting prior", () => {
  assert.equal(evaluate().state, "hit");
  assert.equal(evaluate({...selection,selection:"언더"}).state, "miss");
  const g = game();
  assert.equal(recommendationOutcome(g,live,now).state,"hit");
  assert.equal(gamePhase(g,live,now),"live");
  assert.equal(savedLivePrediction(g,live,now).estimateStatus,"decided_market");
  assert.equal(savedLivePrediction(g,live,now).estimate,null);
  assert.equal(g.prediction_record.result,"pending");
  assert.equal(g.prediction_record.odds,1.8);
  assert.equal(savedLivePrediction(game({...selection,probability:null}),live,now).outcome.state,"hit");
});
test("not yet crossed, equal, ambiguous and split lines cannot be prematurely graded", () => {
  for (const label of ["U 9.5","U 9","U 8.75","U 8.25","","U 8.5 9.5","h U 8.5","U -1.5"])
    assert.equal(evaluate({...selection,label}),null,label);
  assert.equal(evaluate({...selection,market:"승패",selection:"홈"}),null);
});
test("invalid, stale, disrupted or future observations are not decided", () => {
  for (const v of [null,"",true,-1,1.5,"bad"])
    assert.equal(evaluate(selection,{...live,home_score:v}),null);
  for (const patch of [{cancelled:true},{postponed:true},{stale:true},{status:"BEFORE"},{status:"SUSPENDED"},
    {observed_at:null},{observed_at:new Date(now-600001).toISOString()},{observed_at:new Date(now+10000).toISOString()}])
    assert.equal(evaluate(selection,{...live,...patch}),null);
  assert.equal(evaluate(selection,live,"vl"),null);
});
test("soccer excludes extra time and shootouts rather than trusting generic full scores", () => {
  const s = {...selection,label:"U 2.5"};
  assert.equal(evaluate(s,{...live,current_period:2},"sc").state,"hit");
  assert.equal(evaluate(s,{...live,current_period:3,clock:{period:3,phase:"second_half"}},"sc"),null);
  assert.equal(evaluate(s,{...live,current_period:3,regular_time_score:[1,1]},"sc"),null);
});
test("completed half uses explicit period evidence, never minute or full scoreboard", () => {
  const s = {market:"전반승무패",label:"h(전반)",selection:"전반홈"};
  const l = {...live,first_half_complete:true,first_half_score:[1,0],home_score:1,away_score:4};
  assert.equal(evaluate(s,l,"sc").state,"hit");
  assert.equal(evaluate({...s,selection:"전반원정"},l,"sc").state,"miss");
  assert.equal(evaluate({...s,selection:"전반무"},{...l,first_half_score:[0,0]},"sc").state,"hit");
  assert.equal(evaluate(s,{...live,clock:{elapsed_minute:50},period_scores:[{period:1,home:1,away:0}]},"sc"),null);
  assert.equal(evaluate(s,{...l,first_half_complete:false},"sc"),null);
  assert.equal(evaluate({...s,market:"전반승패"},{...l,first_half_score:[0,0]},"bs"),null);
  assert.equal(evaluate({...s,market:"전반언더오버",label:"h U 4.5",selection:"전반언더"},l).state,"hit");
  assert.equal(evaluate({...s,market:"전반핸디캡",label:"h H -1",selection:"전반핸디무",n_way:3},l).state,"hit");
  const g = game(s,l); g.sport="sc";
  assert.equal(gamePhase(g,l,now),"live");
  g.prediction_record.result="hit";
  assert.equal(gamePhase(g,l,now),"live","official half result also does not end full match");
});
test("new score correction retracts display inference; official void takes precedence", () => {
  const g=game();
  assert.equal(recommendationOutcome(g,{...live,home_score:4},now).state,"pending");
  g.prediction_record.result="void";
  assert.equal(recommendationOutcome(g,live,now).state,"void");
});
test("personal record display does not mark payouts settled or require an opening probability", () => {
  const bet={game:{sport:"bs"},selection:{...selection,choice:"오버"},openingProbability:null};
  const l={...live,observed_at:new Date().toISOString()};
  assert.equal(estimateLiveProbability(bet,l).outcome.state,"hit");
  assert.equal(estimateLiveProbability(bet,l).probability,null);
  assert.equal(settleBet(bet,l),null);
  assert.equal(settleBet({...bet,selection:{market:"전반승패",choice:"전반홈"}},
    {...l,finished:true,status:"RESULT"}),null,"missing half score cannot use final score");
});
