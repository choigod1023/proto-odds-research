import { recommendationOutcome } from './pick-result.js';
import { scheduledAt } from './match-status.js';
export function overallAccuracy(data) {
  if(!data) return null;
  const index=data.prediction_performance;
  const canonical=index?.scope==='all_ledger_predictions' && index.records && typeof index.records==='object';
  const games=new Map(), identities=new Map(), events=new Map(), records=new Map();
  for(const game of [...(data.live||[]),...(data.past||[])]) {
    const record=game.prediction_record, id=record?.prediction_snapshot_id;
    if(!id) continue;
    const event=[game.year||new Date(record.captured_at).getUTCFullYear(),game.sport,game.league,game.date,game.home,game.away].join('|');
    identities.set(id,event);
    const eventId=game.decision_snapshot?.event_id || game.event_id;
    if(eventId) events.set(eventId,event);
    const items=games.get(id)||[];items.push(game);games.set(id,items);
    const captured=Date.parse(record.captured_at), kickoff=scheduledAt(game);
    if(!Number.isFinite(captured) || kickoff==null || captured>=kickoff) continue;
    const previous=records.get(event);
    if(!previous || captured>Date.parse(previous.captured_at)) records.set(event,record);
  }
  if(canonical) for(const [event,record] of Object.entries(index.records)) {
    if(record?.prediction_snapshot_id) records.set(identities.get(record.prediction_snapshot_id)||events.get(event)||`ledger:${event}`,record);
  }
  const result={hit:0,miss:0,void:0,pending:0,provisional:0,total:0,from:null,scope:canonical?'ledger_and_saved':'saved_predictions',rows:[],sports:[]};
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
    const linked=games.get(id)||[];
    // Metadata only from this exact revision, never an unrelated current pick.
    const fixture=record.fixture||{};
    const unique=key=>{const values=[...new Set(linked.map(g=>g[key]).filter(v=>v!=null&&v!==''))];return values.length===1?values[0]:null;};
    const meta=Object.fromEntries(['sport','league','home','away','year','date'].map(key=>[key,fixture[key]??unique(key)]));
    const kickoff=record.kickoff || (meta.year && scheduledAt(meta)!=null ? new Date(scheduledAt(meta)).toISOString():null);
    result.rows.push({...meta,id,kickoff,selection:record.selection,market:record.market,label:record.label,
      odds:record.odds,captured_at:record.captured_at,settled_at:record.settled_at,
      state:['hit','miss','void'].includes(state)?state:'pending',provisional});
  }
  result.settled=result.hit+result.miss;
  result.rate=result.settled?result.hit/result.settled:null;
  const groups=new Map();
  for(const row of result.rows){
    const key=row.sport||'unknown';
    if(!groups.has(key)) groups.set(key,{sport:key,hit:0,miss:0,void:0,pending:0,provisional:0,total:0});
    const group=groups.get(key);group[row.state]++;group.total++;if(row.provisional)group.provisional++;
  }
  result.sports=[...groups.values()].map(g=>({...g,settled:g.hit+g.miss,rate:g.hit+g.miss?g.hit/(g.hit+g.miss):null}));
  result.rows.sort((a,b)=>(Date.parse(b.kickoff)||0)-(Date.parse(a.kickoff)||0)||a.id.localeCompare(b.id));
  return result;
}

export const SPORT_NAMES={sc:'축구',bs:'야구',bk:'농구',vl:'배구',ih:'아이스하키',unknown:'종목 정보 없음'};
export function accuracyTime(value){
  if(!value || !/(Z|[+-]\d{2}:\d{2})$/.test(String(value)) || !Number.isFinite(Date.parse(value))) return '시각 정보 없음';
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value));
}
