import {todaySelectionForGame, selectionKey} from './unified-recommendation.js';
import {hitProbabilityOf} from './recommendation-policy.js';
import {decisionFrozen, gamePhase, scheduledAt} from './match-status.js';

/** Display-only ordering of existing highlights. Never promote a new recommendation. */
export function rankTodayPicks(games, memberships, {now=Date.now(), stale=false}={}) {
  if (stale) return [];
  const day=new Date(now+9*3600000).toISOString().slice(0,10);
  const seen=new Set(), rows=[];
  for (const game of games || []) {
    const kickoff=scheduledAt(game);
    if (kickoff==null || kickoff<=now || gamePhase(game,game._liveState,now)!=='upcoming'
      || new Date(kickoff+9*3600000).toISOString().slice(0,10)!==day) continue;
    const frozen=decisionFrozen(game,now);
    if (game._liveOddsChanged && !frozen) continue;
    const {option,membership}=todaySelectionForGame(memberships,game.options,game.round);
    if (!option || membership?.recommended!==true) continue;
    const record=frozen ? game.prediction_record : null;
    if (record && record.selection_id!==option.selection_id) continue;
    const probability=record ? Number(record.probability) : hitProbabilityOf(membership.selection);
    const odds=Number(record ? record.odds : option.배당);
    if (!(probability>0 && probability<1 && odds>1 && Number.isFinite(odds))) continue;
    const event=JSON.stringify([game.year,game.sport,game.league,game.date,game.home,game.away]);
    if (seen.has(event)) continue;
    seen.add(event);
    rows.push({game,option,probability,odds,kickoff,key:selectionKey(option,game.round),
      source:record ? '저장된 사전 확률' : membership.selection?.has_validated_edge===true ? '검증 모델 반영' : '배당 기반 추정'});
  }
  return rows.sort((a,b)=>b.probability-a.probability || a.kickoff-b.kickoff || a.key.localeCompare(b.key));
}
