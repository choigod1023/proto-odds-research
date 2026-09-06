import { recommendationOutcome } from './pick-result.js';
import { scheduledAt } from './match-status.js';
export function overallAccuracy(data) {
  if(!data) return null;
  const index=data.prediction_performance;
  const canonical=index?.scope==='all_ledger_predictions' && index.records && typeof index.records==='object';
  const games=new Map(), identities=new Map(), records=new Map();
  for(const game of [...(data.live||[]),...(data.past||[])]) {
    const record=game.prediction_record, id=record?.prediction_snapshot_id;
    if(!id) continue;
    const event=[game.year||new Date(record.captured_at).getUTCFullYear(),game.sport,game.league,game.date,game.home,game.away].join('|');
    identities.set(id,event);
    const items=games.get(id)||[];items.push(game);games.set(id,items);
    const captured=Date.parse(record.captured_at), kickoff=scheduledAt(game);
    if(!Number.isFinite(captured) || kickoff==null || captured>=kickoff) continue;
    const previous=records.get(event);
    if(!previous || captured>Date.parse(previous.captured_at)) records.set(event,record);
  }
  if(canonical) for(const [event,record] of Object.entries(index.records)) {
    if(record?.prediction_snapshot_id) records.set(identities.get(record.prediction_snapshot_id)||`ledger:${event}`,record);
  }
  const result={hit:0,miss:0,void:0,pending:0,provisional:0,total:0,from:null,scope:canonical?'ledger_and_saved':'saved_predictions'};
  const seen=new Set();
  for(const record of records.values()) {
    const id=record?.prediction_snapshot_id;
    if(!id || seen.has(id)) continue;
    seen.add(id);result.total++;
    if(record.captured_at && (!result.from || record.captured_at<result.from)) result.from=record.captured_at;
    let state=record.result, provisional=false;
    if(!['hit','miss','void'].includes(state)) {
      const outcomes=(games.get(id)||[]).map(g=>recommendationOutcome({...g,prediction_record:record})).filter(o=>['hit','miss','void'].includes(o.state));
      const official=outcomes.filter(o=>o.source==='official');
      const candidates=official.length?official:outcomes;
      if(candidates.length && new Set(candidates.map(o=>o.state)).size===1) {
        state=candidates[0].state;provisional=candidates[0].source==='score';
      }
    }
    result[['hit','miss','void'].includes(state)?state:'pending']++;
    if(provisional && ['hit','miss'].includes(state)) result.provisional++;
  }
  result.settled=result.hit+result.miss;
  result.rate=result.settled?result.hit/result.settled:null;
  return result;
}
