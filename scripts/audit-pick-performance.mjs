/** Read-only public API audit. Run with --capture DIR, or --input DIR for replay. */
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { gzipSync, gunzipSync } from 'node:zlib';
import { overallAccuracy } from '../web/src/lib/overall-accuracy.js';
import { recommendationOutcome } from '../web/src/lib/pick-result.js';
import { scheduledAt } from '../web/src/lib/match-status.js';
import { buildLiveIndex } from '../web/src/lib/live-feed.js';

export function metrics(rows) {
  const settled = rows.filter(r => ['hit', 'miss'].includes(r.state));
  const n = settled.length, hit = settled.filter(r => r.state === 'hit').length;
  const priced = settled.filter(r => Number.isFinite(r.odds) && r.odds > 1);
  const probabilistic = settled.filter(r => Number.isFinite(r.probability) && r.probability > 0 && r.probability < 1);
  const p = n ? hit / n : null, z = 1.96, denominator = 1 + z*z/n;
  const center = (p + z*z/(2*n))/denominator;
  const half = z*Math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denominator;
  return { total: rows.length, settled: n, hit, miss: n-hit,
    pending: rows.filter(r => r.state === 'pending').length,
    void: rows.filter(r => r.state === 'void').length,
    rate: p, wilson95: n ? [center-half, center+half] : null,
    priced: priced.length,
    averageOdds: priced.length ? priced.reduce((s,r)=>s+r.odds,0)/priced.length : null,
    roi: priced.length ? priced.reduce((s,r)=>s+(r.state==='hit'?r.odds:0)-1,0)/priced.length : null,
    probabilityN: probabilistic.length,
    meanProbability: probabilistic.length ? probabilistic.reduce((s,r)=>s+r.probability,0)/probabilistic.length : null,
    brier: probabilistic.length ? probabilistic.reduce((s,r)=>s+(r.probability-Number(r.state==='hit'))**2,0)/probabilistic.length : null };
}

export function extract(picks, scores = {}, odds = {}) {
  const index = buildLiveIndex(scores);
  const synchronize = g => {
    const live = index.get(`${g.home}|${g.away}|${String(g.date).slice(0,5)}`);
    return {...g, _officialMarkets: odds.markets?.[String(g.round)],
      ...(live?.status && live.status !== 'BEFORE' ? {_liveState:live} : {})};
  };
  const data = {...picks, live:(picks.live||[]).map(synchronize), past:(picks.past||[]).map(synchronize)};
  const groups = new Map(), ids = new Map();
  const checks = { gameRows:0, withoutRecord:0, invalidCapture:0, notPregame:0,
    duplicateRows:0, supersededRevisions:0, conflictingOutcomes:[], ledgerOfficialDisagreements:[] };
  for (const game of [...data.live,...data.past]) {
    checks.gameRows++;
    const r = game.prediction_record;
    if (!r?.prediction_snapshot_id) { checks.withoutRecord++; continue; }
    const kickoff = scheduledAt(game), captured = Date.parse(r.captured_at);
    if (!Number.isFinite(captured) || kickoff === null) { checks.invalidCapture++; continue; }
    if (captured >= kickoff) { checks.notPregame++; continue; }
    const event = [game.year||new Date(captured).getUTCFullYear(),game.sport,game.league,game.date,game.home,game.away].join('|');
    const old = groups.get(event);
    if (old) {
      if (old.record.prediction_snapshot_id === r.prediction_snapshot_id) checks.duplicateRows++;
      else checks.supersededRevisions++;
    }
    if (!old || captured > Date.parse(old.record.captured_at)) groups.set(event,{game,record:r,kickoff,event});
    const copies = ids.get(r.prediction_snapshot_id)||[]; copies.push(game); ids.set(r.prediction_snapshot_id,copies);
  }
  const seen = new Set(), rows = [];
  for (const {game,record:r,kickoff,event} of groups.values()) {
    if (seen.has(r.prediction_snapshot_id)) continue;
    seen.add(r.prediction_snapshot_id);
    const outcomes = ids.get(r.prediction_snapshot_id).map(g=>recommendationOutcome(g));
    const official = outcomes.filter(o=>o.source==='official');
    const terminal = outcomes.filter(o=>['hit','miss','void'].includes(o.state));
    const candidates = official.length ? official : terminal;
    const conflict = new Set(candidates.map(o=>o.state)).size > 1;
    if (conflict) checks.conflictingOutcomes.push(r.prediction_snapshot_id);
    const outcome = conflict || !candidates.length ? {state:'pending',source:'unresolved'} : candidates[0];
    if (['hit','miss','void'].includes(r.result)) {
      for (const g of ids.get(r.prediction_snapshot_id)) {
        const independent = recommendationOutcome({...g,prediction_record:{...r,result:'pending'}});
        if (independent.source==='official' && independent.state!==r.result)
          checks.ledgerOfficialDisagreements.push({id:r.prediction_snapshot_id,ledger:r.result,official:independent.state});
      }
    }
    rows.push({event,id:r.prediction_snapshot_id,kickoff:new Date(kickoff).toISOString(),
      day:new Date(kickoff+9*3600000).toISOString().slice(0,10),capturedAt:r.captured_at,
      sport:game.sport,market:r.market,selection:r.selection,label:r.label,
      probability:r.probability,odds:r.odds,state:outcome.state,source:outcome.source});
  }
  return {ui:overallAccuracy(data), rawUi:overallAccuracy(picks), checks, rows:rows.sort((a,b)=>a.kickoff.localeCompare(b.kickoff)||a.id.localeCompare(b.id))};
}

// Fixed policies, never chosen/tuned using the holdout's outcomes.
export const policies = [
  {name:'saved-all',accept:r=>true},
  ...[0.55,0.58,0.60,0.65].map(p=>({name:`probability>=${p.toFixed(2)}`,accept:r=>Number.isFinite(r.probability)&&r.probability>=p})),
  {name:'odds<=1.70',accept:r=>Number.isFinite(r.odds)&&r.odds>1&&r.odds<=1.7},
];
export function compare(rows) {
  // Split on all recorded kickoff days, not on outcome availability or winners.
  const days = [...new Set(rows.map(r=>r.day))].sort();
  const cutoff = days.length>=2 ? days[Math.min(days.length-1,Math.max(1,Math.floor(days.length*0.7)))] : null;
  const parts = {all:rows, ...(cutoff ? {earlier:rows.filter(r=>r.day<cutoff),holdout:rows.filter(r=>r.day>=cutoff)} : {})};
  return {cutoff, days, policies:policies.map(policy=>({name:policy.name,
    ...Object.fromEntries(Object.entries(parts).map(([name,part])=>{
      const selected = part.filter(policy.accept);
      return [name,{...metrics(selected),coverage:part.length?selected.length/part.length:null}];
    }))})), bySport:Object.fromEntries([...new Set(rows.map(r=>r.sport))].sort().map(k=>[k,metrics(rows.filter(r=>r.sport===k))])),
    byMarket:Object.fromEntries([...new Set(rows.map(r=>r.market))].sort().map(k=>[k,metrics(rows.filter(r=>r.market===k))])),
    calibration:[0,0.5,0.55,0.6,0.65,0.7,0.8].map((lo,i,a)=>({lo,hi:a[i+1]??1,
      ...metrics(rows.filter(r=>Number.isFinite(r.probability)&&r.probability>=lo&&r.probability<(a[i+1]??1)))})).filter(x=>x.total)};
}

async function main() {
  const [mode,directory,publishFlag,publishDirectory] = process.argv.slice(2);
  if (!['--capture','--input'].includes(mode)||!directory) throw new Error('Use --capture DIR or --input DIR');
  if (publishFlag && (publishFlag!=='--publish'||!publishDirectory)) throw new Error('Optional: --publish DIR');
  const dir = resolve(directory);
  const publish = publishDirectory ? resolve(publishDirectory) : null;
  if (publish) await mkdir(publish,{recursive:true});
  await mkdir(dir,{recursive:true});
  const endpoints = ['picks','live-scores','live-odds','today-recommendations'];
  const sources = await Promise.all(endpoints.map(async endpoint=>{
    const file = resolve(dir,`${endpoint}.json`);
    let raw;
    if (mode==='--capture') {
      const response=await fetch(`https://proto-odds-collector.fly.dev/api/${endpoint}`,{signal:AbortSignal.timeout(45000)});
      if (!response.ok) throw new Error(`${endpoint}: ${response.status}`);
      raw=await response.text(); JSON.parse(raw); await writeFile(file,raw);
    } else {
      try { raw=await readFile(file,'utf8'); }
      catch(error) {
        if(error.code!=='ENOENT') throw error;
        raw=gunzipSync(await readFile(`${file}.gz`)).toString('utf8');
      }
    }
    if(publish) await writeFile(resolve(publish,`${endpoint}.json.gz`),gzipSync(raw));
    return {endpoint,sha256:createHash('sha256').update(raw).digest('hex'),data:JSON.parse(raw)};
  }));
  const [picks,scores,odds] = sources.map(s=>s.data);
  const audit = extract(picks,scores,odds);
  const independent = metrics(audit.rows);
  const agrees = ['total','hit','miss','pending','void'].every(k=>independent[k]===audit.ui[k]);
  const report = {sourceGeneratedAt:Object.fromEntries(sources.map(s=>[s.endpoint,s.data.generated_at??null])),
    hashes:Object.fromEntries(sources.map(s=>[s.endpoint,s.sha256])),...audit,
    savedRowsAgreeWithUi:agrees, independent, comparison:compare(audit.rows),
    officialOnly:metrics(audit.rows.filter(r=>['ledger','official'].includes(r.source))),
    limitations:[
      'Snapshot audit only: not a complete lifetime ledger when prediction_performance is absent.',
      'Saved event predictions are not proven historical Today Recommendations membership; policy comparisons are retrospective filters, not a backtest of that ranker.',
      'Only original saved probability/odds/selection used. API snapshots are sequentially generated, not atomic; browser cached live observations may differ.',
      'Latest pregame revision policy matches current UI. Capture timestamp is not independently verified against an immutable ledger in this report.',
      'Score outcomes are provisional. Missing/unsettled outcomes may be nonrandom. Wilson intervals ignore cross-game dependence.',
      'Fixed thresholds evaluated descriptively; holdout is not sufficient evidence for production changes or calibrated probability uplift.',
      'ROI uses one unit per valid priced settled pick, excludes voids, fees and execution differences.'
    ]};
  await writeFile(resolve(publish||dir,'report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({...report,rows:undefined,comparison:{...report.comparison,bySport:undefined,byMarket:undefined,calibration:undefined}},null,2));
}
if (process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href) await main();
