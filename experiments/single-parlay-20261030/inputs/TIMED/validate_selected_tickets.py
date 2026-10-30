"""Independent streaming validation. Does not import or run ticket-selection code."""
import sys
sys.dont_write_bytecode=True
import bisect
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D,ROUND_FLOOR,ROUND_CEILING
from functools import lru_cache
import gzip,hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent/'independent_ticket_validation.json'
SESSIONS=ROOT/'.local-test/RESULTS/sessions.jsonl.gz'
SUMMARY=ROOT/'.local-test/RESULTS/summary.json.gz'
MANIFEST=ROOT/'.local-test/RESULTS/manifest.json'
PROTOCOL=ROOT/'docs/research/2026-10-30-single-parlay-protocol.json'
KST=timezone(timedelta(hours=9))

def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()

@lru_cache(maxsize=200000)
def stamp(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone_required')
    return d.timestamp()

def rows(p):
    with gzip.open(p,'rt',encoding='utf8') as f:
        for l in f:
            if l.strip():yield json.loads(l,parse_float=D)

def multiply(vals):
    p=D(1)
    for v in vals:p*=D(str(v))
    return p

def offered(prices):
    p=multiply(prices)
    if len(prices)==1:return p
    cents=(p*100).to_integral_value(rounding=ROUND_FLOOR)/100
    return (cents*10).to_integral_value(rounding=ROUND_CEILING)/10

def settlement(legs,stake,components=False):
    if any(l.get('hit') is None and not l.get('refund',False) for l in legs):
        return dict(status='pending',net=None,hit=None,refunded=False)
    stake=D(str(stake))
    if components:
        portion=stake/D(len(legs))
        returned=sum((portion*(D(1) if l.get('refund') else D(str(l['odds'])) if l['hit'] else D(0)) for l in legs),D(0))
    elif any(not l['hit'] and not l.get('refund') for l in legs):returned=D(0)
    else:returned=stake*offered([D(1) if l.get('refund') else l['odds'] for l in legs])
    return dict(status='settled',net=returned-stake,hit=all(l['hit'] or l.get('refund',False) for l in legs),refunded=all(l.get('refund',False) for l in legs))

def side(c):return c.get('selection') if c.get('selection') is not None else c.get('label')

def groupkey(s):
    return (s['cohort'],s['model'],s['league'],s['ranking'],s['context'],tuple(s.get('models_compared',[])))

def main():
    start=datetime.now(timezone.utc).isoformat()
    protocol=json.loads(PROTOCOL.read_text(encoding='utf8'))
    manifest=json.loads(MANIFEST.read_text(encoding='utf8'))
    budget=protocol['budget_per_session_per_strategy_krw']
    paths=[SESSIONS,SUMMARY,MANIFEST,PROTOCOL,ROOT/'scripts/single_parlay_research.py']+[ROOT/i['path'] for i in manifest['inputs']]
    hashes={p.relative_to(ROOT).as_posix():digest(p) for p in paths}
    checks=Counter();failures=Counter();examples=defaultdict(list);counts=Counter();cohorts=defaultdict(Counter)
    failed_sessions=defaultdict(set);failed_tickets=defaultdict(set)
    def check(name,ok,example):
        checks[name]+=1
        if not ok:
            failures[name]+=1
            if len(examples[name])<5:examples[name].append(example)
            if 'session_line' in example:failed_sessions[name].add(example['session_line'])
            if 'strategy' in example:failed_tickets[name].add((example['session_line'],example['strategy']))
    frames={};revisions=defaultdict(list)
    keep=('choice_id','market','label','selection','line','issue_year','round','game_no','odds','hit','refund','result_status')
    for entry in manifest['inputs']:
        path=ROOT/entry['path'];source_count=0
        check('manifest_input_hash',hashes[path.relative_to(ROOT).as_posix()]==entry['sha256'],{'path':entry['path'],'manifest':entry['sha256'],'actual':hashes[path.relative_to(ROOT).as_posix()]})
        for f in rows(path):
            source_count+=1;cohort=f['cohort'];snap=f['snapshot_id'];fk=(cohort,snap)
            check('source_snapshot_unique',fk not in frames,{'snapshot':snap})
            choices={}
            for c in f['choices']:
                ck=(str(c['offer_id']),side(c))
                check('source_choice_key_unique',ck not in choices,{'snapshot':snap,'offer':ck})
                choices[ck]=(tuple(c.get(k) for k in keep),c.get('scores',{}),tuple(c.get('probability_models',f.get('probability_models',[]))))
            frames[fk]={k:f.get(k) for k in ('event_id','kickoff','available_at','quote_observed_at','home','away','league','availability_proven')}
            frames[fk]['choices']=choices
            revisions[(cohort,f['event_id'],stamp(f['kickoff']))].append((stamp(f['available_at']),f.get('ledger_sequence',0),snap))
        check('manifest_source_frame_count',source_count==entry['frames'],{'path':entry['path'],'actual':source_count,'manifest':entry['frames']})
        print('indexed',entry['path'],source_count,flush=True)
    for r in revisions.values():r.sort()
    revision_times={k:[r[0] for r in v] for k,v in revisions.items()}
    expected_summary=defaultdict(Counter);cross=defaultdict(list);session_seen=set();age_stats=defaultdict(Counter)
    age_values=defaultdict(list);age_reference_examples=[];age_reference_tickets=set();age_reference_sessions=set()
    ticket_status=Counter();unique_tickets=set()
    for lineno,s in enumerate(rows(SESSIONS),1):
        counts['sessions']+=1;cohorts[s['cohort']]['sessions']+=1
        sk=groupkey(s);sig=sk[-1]
        session_identity=(sk,s['day'],tuple(s['issue']))
        ex=dict(session_line=lineno,cohort=s['cohort'],model=s['model'],context=s['context'])
        check('one_session_per_strategy_context',session_identity not in session_seen,ex);session_seen.add(session_identity)
        expected_summary[sk]['selected_sessions']+=1
        complete=s.get('cross_model_complete',True) and all(t.get('net') is not None for t in s['tickets'].values())
        expected_summary[sk]['jointly_settled_sessions']+=bool(complete)
        placements=set();stakes=set()
        for name,t in s['tickets'].items():
            counts['tickets']+=1;cohorts[s['cohort']]['tickets']+=1;ticket_status[t['status']]+=1
            tex=dict(ex,strategy=name,placement=t['placement'])
            placements.add(t['placement']);stakes.add(t['stake']);placement=stamp(t['placement']);legs=t['legs']
            check('ticket_strategy_matches_key',t['strategy']==name,tex)
            check('budget_equal_12000',t['stake']==budget,tex)
            check('placement_on_30min_grid',placement%1800==0,tex)
            check('leg_count',len(legs)==protocol['strategies'][name]['legs'],tex)
            check('all_legs_exact_session_issue',all((l.get('issue_year'),l.get('round'))==tuple(s['issue']) for l in legs),tex)
            complete_coordinates=True;valid_issue=True
            for l in legs:
                try:
                    valid_issue=valid_issue and 2000<=int(l['issue_year'])<=2100 and int(l['round'])>=1
                    complete_coordinates=complete_coordinates and int(l['game_no'])>=1
                except (KeyError,TypeError,ValueError):
                    complete_coordinates=False
                    try:int(l['issue_year']);int(l['round'])
                    except (KeyError,TypeError,ValueError):valid_issue=False
            check('issued_product_coordinates_complete',valid_issue and complete_coordinates,dict(tex,coordinates=[(l.get('issue_year'),l.get('round'),l.get('game_no')) for l in legs]))
            check('valid_known_issue_round',valid_issue,dict(tex,issue=s['issue']))
            if not (valid_issue and complete_coordinates):
                check('unverified_coordinates_kept_pending',t.get('status')=='pending' and t.get('net') is None and all(l.get('hit') is None and not l.get('refund') for l in legs if l.get('game_no') is None),tex)
            event_ids=[l['event_id'] for l in legs]
            check('unique_event_ids',len(event_ids)==len(set(event_ids)),tex)
            teamnames=[str(l[k]).strip().casefold() for l in legs for k in ('home','away')]
            check('no_shared_teams',all(teamnames) and len(teamnames)==len(set(teamnames)),tex)
            physical=[(stamp(l['kickoff']),tuple(sorted([str(l['home']).strip().casefold(),str(l['away']).strip().casefold()]))) for l in legs]
            check('unique_physical_events',len(physical)==len(set(physical)),tex)
            unique_tickets.add((s['cohort'],s['model'],s['ranking'],name,t['placement'],tuple((l['snapshot_id'],l['offer_id'],side(l)) for l in legs)))
            rule=protocol['strategies'][name]
            for l in legs:
                counts['legs']+=1;cohorts[s['cohort']]['legs']+=1
                lex=dict(tex,snapshot=l['snapshot_id'],offer=l['offer_id'],selection=side(l))
                check('available_before_placement',stamp(l['available_at'])<=placement,lex)
                check('kickoff_at_least_30min_after_placement',stamp(l['kickoff'])>=placement+1800,lex)
                day=datetime.fromisoformat(l['kickoff'].replace('Z','+00:00')).astimezone(KST).date().isoformat()
                check('kickoff_day_matches_session',day==s['day'],lex)
                price=D(str(l['odds']));lo=D(str(rule['min_odds']));hi=rule['max_odds']
                check('strategy_price_band',price>=lo and (hi is None or price<=D(str(hi)) if rule.get('max_inclusive',True) else price<D(str(hi))),lex)
                f=frames.get((s['cohort'],l['snapshot_id']))
                check('source_snapshot_membership',f is not None,lex)
                if f is None:continue
                check('source_frame_identity_and_times',all(l.get(k)==f.get(k) for k in ('event_id','kickoff','available_at','home','away','quote_observed_at')) and f['league']==s['league'],lex)
                c=f['choices'].get((str(l['offer_id']),side(l)))
                check('source_choice_membership_snapshot_offer_selection',c is not None,lex)
                if c is None:continue
                original,scores,models=c
                check('source_choice_fields_prices_outcomes',original==tuple(l.get(k) for k in keep),lex)
                check('source_model_score',l.get('score')==scores.get(s['model']),lex)
                check('probability_model_identity',l.get('probability')==(s['model'] in models),lex)
                if s['ranking']=='estimated_ev':check('no_vote_score_used_as_ev_probability',s['model'] in models,lex)
                check('unsettled_source_has_null_hit',l.get('result_status') not in ('pending','conflict') or l.get('hit') is None,lex)
                rk=(s['cohort'],l['event_id'],stamp(l['kickoff']))
                pos=bisect.bisect_right(revision_times[rk],placement)-1
                expected=revisions[rk][pos][2] if pos>=0 else None
                check('latest_full_event_revision',l['snapshot_id']==expected,dict(lex,expected_snapshot=expected))
                if s['cohort']=='saved_pregame':
                    check('saved_time_source_availability_proven',f.get('availability_proven') is True,lex)
                    observed=f.get('quote_observed_at')
                    check('primary_quote_observed_time_present',observed is not None,lex)
                    if observed:
                        age=(placement-stamp(observed))/60
                        check('primary_quote_not_future',age>=0,dict(lex,quote_age_minutes=age))
                        age_values[s['cohort']].append(age)
                        if age>120:
                            age_reference_tickets.add((lineno,name));age_reference_sessions.add(lineno)
                            if len(age_reference_examples)<5:age_reference_examples.append(dict(lex,quote_age_minutes=age))
                        age_stats[s['cohort']]['legs']+=1;age_stats[s['cohort']]['older_than_120min_reference_only']+=age>120
                        age_stats[s['cohort']]['max_minutes']=max(age_stats[s['cohort']]['max_minutes'],age)
            check('quoted_odds_independent_decimal',D(str(t['quoted_odds']))==offered([l['odds'] for l in legs]),tex)
            expected=settlement(legs,t['stake'])
            check('actual_net_independent_decimal',t.get('net')==expected['net'],dict(tex,expected_net=expected['net'],actual_net=t.get('net')))
            check('settlement_status_hit_refund',all(t.get(k)==expected[k] for k in ('status','hit','refunded')),tex)
            if len(legs)>1:
                counts['component_controls']+=1
                check('component_stake_integer_1000_multiple',D(t['stake'])/len(legs)%1000==0,tex)
                ec=settlement(legs,t['stake'],True);actual=t.get('equal_budget_component_singles',{})
                check('component_singles_independent_decimal',all(actual.get(k)==v for k,v in ec.items()),dict(tex,expected=ec,actual=actual))
        check('equal_stakes_across_session_strategies',len(stakes)==1 and stakes=={budget},ex)
        check('same_checkpoint_across_session_strategies',len(placements)==1,ex)
        if s['context'].startswith('model_common:'):
            check('cross_model_signature_declared',len(sig)>=2 and len(sig)==len(set(sig)) and s['model'] in sig,ex)
            ck=(s['cohort'],s['league'],s['day'],tuple(s['issue']),s['ranking'],s['context'],sig)
            cross[ck].append((s['model'],tuple(sorted(placements)),s.get('cross_model_complete'),all(t.get('net') is not None for t in s['tickets'].values()),tuple(sorted((n,t['stake']) for n,t in s['tickets'].items()))))
        if lineno%50000==0:print('validated sessions',lineno,'tickets',counts['tickets'],'failures',dict(failures),flush=True)
    signatures=Counter()
    for ck,items in cross.items():
        sig=ck[-1];signatures['+'.join(sig)]+=1;models=[i[0] for i in items]
        check('cross_model_group_complete_model_membership',len(models)==len(sig) and set(models)==set(sig),{'group':ck})
        check('cross_model_same_checkpoint',len({i[1] for i in items})==1,{'group':ck})
        check('cross_model_equal_stakes',len({i[4] for i in items})==1,{'group':ck})
        check('cross_model_complete_flag_matches_all_outcomes',all(i[2]==all(j[3] for j in items) for i in items),{'group':ck})
    try:
        with gzip.open(SUMMARY,'rt',encoding='utf8') as f:summary=json.load(f,parse_float=D)
    except (OSError,EOFError,ValueError) as exc:
        summary=[]
        check('summary_readable',False,{'error':str(exc)})
    seen_summary=set();bases=defaultdict(set)
    for r in summary:
        key=groupkey(r);ex={'summary_key':key}
        check('summary_unique_model_signature_key',key not in seen_summary,ex)
        seen_summary.add(key);bases[key[:-1]].add(key[-1])
        expected=expected_summary.get(key)
        check('summary_model_signature_membership',expected is not None,ex)
        if expected:
            check('summary_selected_count_by_exact_signature',r['selected_sessions']==expected['selected_sessions'],ex)
            check('summary_settled_count_by_exact_signature',r['jointly_settled_sessions']==expected['jointly_settled_sessions'],ex)
    check('summary_covers_every_signature_group',seen_summary==set(expected_summary),{'missing_count':len(set(expected_summary)-seen_summary),'extra_count':len(seen_summary-set(expected_summary))})
    check('session_count_matches_manifest',counts['sessions']==sum(i['sessions'] for i in manifest['audit']),{'actual':counts['sessions']})
    after={p.relative_to(ROOT).as_posix():digest(p) for p in paths}
    check('input_artifacts_stable_during_validation',hashes==after,{'changed':[p for p in hashes if hashes[p]!=after[p]]})
    check('manifest_code_hash_matches_current_code',hashes['scripts/single_parlay_research.py']==manifest['code_sha256'],{'manifest':manifest['code_sha256'],'actual':hashes['scripts/single_parlay_research.py']})
    counts.update(source_frames=len(frames),source_choices=sum(len(f['choices']) for f in frames.values()),unique_tickets=len(unique_tickets),cross_model_groups=len(cross),summary_rows=len(summary),summary_base_groups_with_multiple_model_signatures=sum(len(sigs)>1 for sigs in bases.values()))
    freshness={}
    for cohort,values in age_values.items():
        values.sort()
        def percentile(q):
            at=(len(values)-1)*q;lo=int(at);hi=min(lo+1,len(values)-1)
            return values[lo]+(values[hi]-values[lo])*(at-lo)
        freshness[cohort]=dict(frozen_protocol_max_quote_age_minutes=None,pass_gate=False,
            scope='Selected leg instances, including repetitions across strategy/model/comparison contexts.',
            reference_threshold_minutes=120,reference_threshold_is_contractual=False,
            **dict(age_stats[cohort]),
            older_than_120min_pct_reference_only=100*sum(v>120 for v in values)/len(values),
            affected_ticket_instances_reference_only=len(age_reference_tickets),affected_sessions_reference_only=len(age_reference_sessions),
            min_minutes=values[0],percentiles_minutes={str(q):percentile(q/100) for q in (50,75,90,95,99)},
            buckets={name:sum(lo<=v<hi for v in values) for name,lo,hi in [('under30',float('-inf'),30),('30_to_60',30,60),('60_to_120',60,120),('120_to_360',120,360),('360_to_1440',360,1440),('1440_or_more',1440,float('inf'))]},
            examples_over_reference_threshold=age_reference_examples)
    report=dict(started_at=start,completed_at=datetime.now(timezone.utc).isoformat(),independent=True,
        selection_code_imported=False,roi_search_performed=False,all_selected_sessions_examined=True,
        overall_pass=not bool(failures),all_core_ticket_invariants_pass=not bool(failures),
        counts=dict(counts),cohorts={k:dict(v) for k,v in cohorts.items()},ticket_status=dict(ticket_status),
        checks={k:{'classification':'integrity',
                   'checked':v,'failures':failures.get(k,0),'affected_sessions':len(failed_sessions[k]),'affected_tickets':len(failed_tickets[k])} for k,v in sorted(checks.items())},failure_examples=dict(examples),
        cross_model_signatures=dict(signatures),quote_freshness_diagnostic=freshness,
        artifact_sha256_before=hashes,artifact_sha256_after=after,validator_sha256=digest(__file__),
        arithmetic_self_checks_passed=8,
        methodology=['Streaming full session ledger, no sampling. Source membership uses cohort/snapshot/offer/selection.',
            'Independent Decimal arithmetic: raw product, floor to 2 decimals, ceil to 1 decimal for parlays; single prices unchanged.',
            'Unknown non-refund leg leaves ticket pending. Refund leg price becomes 1.00; original leg count retains parlay rounding, per frozen protocol.',
            'Components divide the same 12000-KRW ticket budget equally over its exact legs.',
            'Latest revision checked across all leagues using normalized event/kickoff and available_at/ledger_sequence/snapshot tie order.',
            'Summary keys and counts checked with exact ordered models_compared signature; no 2-model/4-model pooling.'],
        limitations=['Source membership verifies the provided normalized inputs, not independent operator settlement or external pregame availability.',
            'Historical/vote proxy available_at timestamps are hypothetical: arithmetic timing pass does not certify buyability.',
            'Complete issued coordinates, including nonempty game_no, are required at selection time under the corrected identity gate.',
            'The frozen protocol has no maximum quote-age limit. Freshness is diagnostic only; the 120-minute reference is not a pass/fail gate and does not filter tickets.',
            'No independent search of ticket optimality or earliest feasible checkpoint; no model calibration certification.'])
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
    print(json.dumps({'output':str(OUT),'overall_pass':report['overall_pass'],'counts':report['counts'],'failures':dict(failures)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
