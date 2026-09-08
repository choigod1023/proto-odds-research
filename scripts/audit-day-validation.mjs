/** Read-only replay of captured public artifacts. Never trains on today's labels. */
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {extract,metrics} from './audit-pick-performance.mjs';
import {recommendationResults} from '../web/src/lib/recommendation-results.js';
import {buildLiveIndex} from '../web/src/lib/live-feed.js';

export function kstDay(value) {
  const t=Date.parse(value);
  return Number.isFinite(t)?new Date(t+9*3600000).toISOString().slice(0,10):null;
}

export function validHistory(entry, day, now) {
  const times=[entry?.kickoff_at,entry?.published_at,entry?.recorded_at].map(Date.parse);
  return Boolean(entry?.id && typeof entry.recommended==='boolean' && times.every(Number.isFinite)
    && kstDay(entry.kickoff_at)===day && times[0]<=now
    && Math.max(times[1],times[2])<times[0]-30*60000 && Math.max(times[1],times[2])<=now);
}

export function auditDay(picks,scores,odds,today,day,now) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || kstDay(day+'T00:00:00+09:00')!==day
      || !Number.isFinite(now)) throw new Error('Valid KST date and explicit as-of required');
  const live=buildLiveIndex(scores), games=[...(picks.live||[]),...(picks.past||[])];
  const enrich=(r,g)=>{
    const l=g&&live.get(`${g.home}|${g.away}|${String(g.date).slice(0,5)}`);
    return {...r,league:g?.league??r.league,home:g?.home??r.home,away:g?.away??r.away,
      finalScore:l?.finished?[l.home_score,l.away_score]:null,
      feedFinished:l?.finished===true,feedCancelled:l?.cancelled===true,
      feedStatus:l?.status??null,feedObservedAt:l?.observed_at??null};
  };
  const saved=extract(picks,scores,odds).rows.filter(r=>r.day===day).map(r=>enrich(r,
    games.find(g=>g.prediction_record?.prediction_snapshot_id===r.id)));
  const history=[],rejected=[],events=new Set();
  for (const entry of Object.values(today.recommendation_history||{})) {
    if (kstDay(entry.kickoff_at)!==day) continue;
    if (!validHistory(entry,day,now)) {rejected.push(entry.id??null);continue;}
    const event=JSON.stringify([entry.kickoff_at,entry.sport,entry.league,entry.home,entry.away]);
    if (events.has(event)) throw new Error('Conflicting history copies for one event');
    events.add(event);
    // One entry avoids the UI's latest-ten cap. Force inclusion only for grading;
    // original membership remains entry.recommended in the audit output.
    const result=recommendationResults({recommendation_history:{[entry.id]:{...entry,recommended:true}}},picks,odds,now);
    const state=result.settled[0]?.outcome.state ?? (result.void?'void':'pending');
    const g=games.find(g=>g.home===entry.home&&g.away===entry.away&&g.league===entry.league
      &&g.sport===entry.sport&&g.date===entry.date&&String(g.round)===String(entry.round));
    history.push(enrich({...entry,day,state,source:state==='pending'?'unresolved':'official',
      storedState:entry.result??'pending',recordKind:'pre-T30 candidate/history'},g));
  }
  const recommended=history.filter(r=>r.recommended);
  const group=(rows,field)=>Object.fromEntries([...new Set(rows.map(r=>r[field]))].sort()
    .map(k=>[k,metrics(rows.filter(r=>r[field]===k))]));
  const fullTimeFinished=r=>r.feedFinished&&!r.feedCancelled;
  return {day,asOf:new Date(now).toISOString(),newModelTested:false,
    newModelBlocker:'CatBoost has not been trained. API history preserves one candidate per event, not the complete pregame market/feature universe; authenticated DB export is unavailable.',
    sources:{picks:picks.generated_at,scores:scores.generated_at,odds:odds.generated_at,today:today.generated_at},
    rejectedHistoryIds:rejected,
    saved:{metrics:metrics(saved),byLeague:group(saved,'league'),bySport:group(saved,'sport'),rows:saved},
    frozenCandidates:{metrics:metrics(history),byLeague:group(history,'league'),rows:history},
    recommended:{metrics:metrics(recommended),byLeague:group(recommended,'league'),bySport:group(recommended,'sport'),rows:recommended},
    completedGames:{saved:metrics(saved.filter(fullTimeFinished)),recommended:metrics(recommended.filter(fullTimeFinished))},
    limits:[
      'Date means KST scheduled kickoff date, not proven final-whistle date. Completed subset requires a matched finished/non-cancelled feed.',
      'Saved event picks and the pre-T30 recommendation history are different revisions and must not be mixed.',
      'Public timestamp fields are validated but not independently checked against immutable DB ledger; source artifacts are not atomic.',
      'One day is descriptive only: no model uplift or generalizable calibration claim; Wilson intervals ignore dependence.',
      'Original odds and probabilities only. ROI is flat single stakes on settled priced picks, excluding voids and execution costs.',
      'Official period results are resolved locally by the corrected code. Production DB is not modified.'
    ]};
}

async function main(){
  const [dir,day,asOf,output]=process.argv.slice(2);
  if(!dir||!day||!asOf||!output)throw new Error('Usage: node scripts/audit-day-validation.mjs INPUT_DIR YYYY-MM-DD AS_OF_ISO NEW_OUTPUT');
  const inputs=['picks','live-scores','live-odds','today-recommendations'];
  if(inputs.some(n=>resolve(dir,n+'.json')===resolve(output)))throw new Error('Output cannot replace input');
  const raw=await Promise.all(inputs.map(n=>readFile(resolve(dir,n+'.json'),'utf8')));
  const result=auditDay(...raw.map(JSON.parse),day,Date.parse(asOf));
  result.hashes=Object.fromEntries(inputs.map((n,i)=>[n,createHash('sha256').update(raw[i]).digest('hex')]));
  await writeFile(resolve(output),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
  console.log(JSON.stringify({saved:result.saved.metrics,frozen:result.frozenCandidates.metrics,
    recommended:result.recommended.metrics,leagues:result.recommended.byLeague,completed:result.completedGames}));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)await main();
