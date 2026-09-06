import { recommendationOutcome } from './pick-result.js';
export function overallAccuracy(data) {
  const index=data?.prediction_performance;
  if(index?.scope !== 'all_ledger_predictions' || !index.records || typeof index.records !== 'object') return null;
  const games=new Map();
  for(const game of [...(data.live||[]),...(data.past||[])]) {
    const id=game.prediction_record?.prediction_snapshot_id;
    if(id) {
      const items=games.get(id)||[];items.push(game);games.set(id,items);
    }
  }
  const result={hit:0,miss:0,void:0,pending:0,provisional:0,total:0,from:null};
  const seen=new Set();
  for(const record of Object.values(index.records)) {
    const id=record?.prediction_snapshot_id;
    if(!id || seen.has(id)) continue;
    seen.add(id);result.total++;
    if(record.captured_at && (!result.from || record.captured_at<result.from)) result.from=record.captured_at;
    let state=record.result;
    let provisional=false;
    if(!['hit','miss','void'].includes(state)) {
      const outcomes=(games.get(id)||[]).map(g=>recommendationOutcome({...g,prediction_record:record})).filter(o=>['hit','miss','void'].includes(o.state));
      // Duplicate listings must agree; official results take precedence over score estimates.
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
