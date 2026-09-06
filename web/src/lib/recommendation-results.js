import { recommendationOutcome } from './pick-result.js';

/** Only archived pre-T30 roster entries count. Never infer old membership. */
export function recommendationResults(today, data, odds, now = Date.now()) {
  const all = [];
  for (const entry of Object.values(today?.recommendation_history || {})) {
    const kickoff = Date.parse(entry.kickoff_at), published = Date.parse(entry.published_at);
    const recorded = Date.parse(entry.recorded_at);
    if (entry.recommended !== true || !entry.id || !Number.isFinite(kickoff)
        || !Number.isFinite(published) || !Number.isFinite(recorded)
        || Math.max(published, recorded) >= kickoff - 30*60000 || published > now || recorded > now) continue;
    const game = [...(data?.live || []), ...(data?.past || [])].find(g =>
      g.home === entry.home && g.away === entry.away && g.sport === entry.sport
      && g.league === entry.league && g.date === entry.date && String(g.round) === String(entry.round));
    const record = {prediction_snapshot_id:entry.id,selection_id:entry.id,
      market:entry.market,label:entry.market_label || '',selection:entry.sel,
      odds:entry.odds,probability:entry.probability,captured_at:entry.published_at,result:'pending'};
    // Use the archived selection and original bookmaker game number, not a new pick.
    const archivedGame = {...entry, year:new Date(kickoff+9*3600000).getUTCFullYear(),
      prediction_record:record, options:[{selection_id:entry.id,게임번호:entry.game_no}],
      _officialMarkets: odds?.markets?.[String(entry.round)]};
    let outcome = recommendationOutcome(archivedGame);
    if (outcome.source !== 'official' && entry.result_source === 'official'
        && ['hit','miss','void'].includes(entry.result)) outcome = {state:entry.result,source:'official'};
    if (game?.prediction_record?.selection_id && game.prediction_record.market === record.market
        && (game.prediction_record.label || '') === record.label
        && game.prediction_record.selection === record.selection
        && game.prediction_record.odds === record.odds) {
      const option = game.options?.find(o=>o.selection_id===game.prediction_record.selection_id);
      if (typeof option?.적중 === 'boolean' && outcome.source !== 'official')
        outcome = {state:option.적중?'hit':'miss',source:'official'};
    }
    if (outcome.source !== 'official' || kickoff > now) outcome = {state:'pending'};
    all.push({...entry,game, outcome, kickoff});
  }
  const unique = [...new Map(all.map(r=>[r.id,r])).values()];
  const settled = unique.filter(r=>['hit','miss'].includes(r.outcome.state))
    .sort((a,b)=>b.kickoff-a.kickoff || a.id.localeCompare(b.id)).slice(0,10);
  return {settled,hit:settled.filter(r=>r.outcome.state==='hit').length,
    upcoming:unique.filter(r=>r.outcome.state==='pending'&&r.kickoff>now).length,
    pending:unique.filter(r=>r.outcome.state==='pending'&&r.kickoff<=now).length,
    void:unique.filter(r=>r.outcome.state==='void').length};
}
