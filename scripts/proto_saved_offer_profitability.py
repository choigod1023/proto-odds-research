"""Evaluate saved full market rosters using original pregame prices.

Never backfills an older model when the latest event snapshot lacks it. Exact
issued identity, period, line and roster are required. Market probabilities,
saved final references and unvalidated score shadows remain separate models.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
import random
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.proto_all_pick_research import LABELS,SEL_NAMES,VOID_RESULTS,LINE,STAKE,parse_row,read_source,summarize,refund_label

KST=timezone(timedelta(hours=9))


def aware(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('timezone required')
    return t.astimezone(KST)


def line_key(family,line,label=''):
    if family.removeprefix('전반') not in ('핸디캡','언더오버'):return None
    if line is None:
        match=LINE.search(label)
        if not match:raise ValueError('missing line')
        line=match.group()
    number=Decimal(str(line))
    if not number.is_finite():raise ValueError('invalid line')
    return str(number.normalize())


def issued_key(year,rnd,game):
    values=int(year),int(rnd),int(game)
    if not 2000<=values[0]<=2100 or values[1]<1 or values[2]<1:raise ValueError('invalid identity')
    return values


def latest_snapshots(snapshots,freeze_minutes=30):
    """Pick latest eligible event snapshot BEFORE any option/model/result checks."""
    latest={};ambiguous=set();audit=Counter()
    for r in snapshots:
        audit['snapshots_input']+=1
        try:
            kickoff,captured,asof=aware(r['kickoff']),aware(r['captured_at']),aware(r['as_of'])
            observed=aware(r['market_observed_at']) if r.get('market_observed_at') else asof
            if observed>captured or asof>captured or captured>kickoff-timedelta(minutes=freeze_minutes):
                audit['snapshot_time_excluded']+=1;continue
            key=(r['event_id'],kickoff.isoformat())
        except (KeyError,ValueError,TypeError):
            audit['snapshot_invalid_time']+=1;continue
        old=latest.get(key)
        if old is None or captured>aware(old['captured_at']):
            latest[key]=r;ambiguous.discard(key)
        elif captured==aware(old['captured_at']) and r['record_hash']!=old['record_hash']:
            ambiguous.add(key)
    for key in ambiguous:latest.pop(key)
    audit['ambiguous_latest_events']=len(ambiguous)
    audit['latest_event_snapshots']=len(latest)
    audit['latest_empty_roster_events']=sum(r.get('saved_options_count',0)==0 for r in latest.values())
    return {r['record_hash']:r for r in latest.values()},audit


def result_index(archives,extra=()):
    groups=defaultdict(dict);audit=Counter()
    for source,raw in archives:
        try:r=parse_row(raw,source)
        except ValueError:
            audit['archive_shape_excluded']+=1;continue
        if r['status']=='settled':status,winner='settled',r['winner']
        elif refund_label(r['family'],r['n_way'],r['sport'],r['result']):status,winner='refund',None
        else:
            audit['archive_unconfirmed_excluded']+=1;continue
        key=(*issued_key(r['season'],r['round'],r['game_no']),r['family'],r['line'])
        body=dict(family=r['family'],line=r['line'],n_way=r['n_way'],status=status,winner=winner,
                  result=r['result'],event_id=None,kickoff=r['kickoff'],home=r['home'],away=r['away'],
                  league=r['league'],source=source)
        signature=(status,winner,r['n_way'],r['kickoff'],r['home'],r['away'])
        groups[key][signature]=body
    for r in extra:
        try:
            if not r.get('issued_product_identity_complete') or not r.get('labels_are_original_saved'):continue
            family=r['market'];line=line_key(family,r.get('line'),r.get('label',''))
            n=len(r['saved_same_product_offer_roster'])
            if (family,n) not in SEL_NAMES:continue
            label=r['official_result_label'];base=family.removeprefix('전반')
            if refund_label(family,n,(r.get('fixture') or {}).get('sport'),label):status,winner='refund',None
            elif label in LABELS.get((base,n),{}):status,winner='settled',LABELS[(base,n)][label]
            else:continue
            key=(*issued_key(r['year'],r['round'],r['game_no']),family,line)
            body=dict(family=family,line=line,n_way=n,status=status,winner=winner,result=label,
                      event_id=r['event_id'],kickoff=aware(r['kickoff']).replace(tzinfo=None).isoformat(),
                      home=None,away=None,league=None,source=r['source_file'],
                      official_result_record_hash=r['source_record_hash'])
            signature=(status,winner,n,body['kickoff'],None,None)
            groups[key][signature]=body
            audit['extra_issued_result_records']+=1
        except (KeyError,ValueError,TypeError):audit['extra_invalid']+=1
    index={}
    for key,rows in groups.items():
        # Different copies may add identity evidence without changing the label.
        semantics={(v['status'],v['winner'],v['n_way'],v['kickoff']) for v in rows.values()}
        names={(v['home'],v['away']) for v in rows.values() if v['home'] is not None}
        event_ids={v['event_id'] for v in rows.values() if v['event_id'] is not None}
        if len(semantics)!=1 or len(names)>1 or len(event_ids)>1:
            audit['conflicting_issued_results']+=1;continue
        body=dict(next(iter(rows.values())))
        if event_ids:body['event_id']=next(iter(event_ids))
        if names:body['home'],body['away']=next(iter(names))
        index[key]=body
    audit['issued_results_available']=len(index)
    return index,audit


def joined_rosters(snapshots,options,index,freeze_minutes=30):
    chosen,audit=latest_snapshots(snapshots,freeze_minutes)
    groups=defaultdict(list)
    for r in options:
        if r['record_hash'] not in chosen:continue
        audit['options_in_latest_rosters']+=1
        try:
            if not r.get('identity_complete') or not (r.get('identity_join') or {}).get('identity_recomputation_pass'):
                audit['option_no_verified_issued_identity']+=1;continue
            identity=r['identity_join'];fixture=identity['fixture']
            family=r['market'];n=int(identity['n_way'])
            if (family,n) not in SEL_NAMES:raise ValueError('unsupported family')
            line=line_key(family,r.get('line'),r.get('label',''))
            key=(*issued_key(fixture['year'],fixture['round'],identity['game_no']),family,line)
            label=index.get(key)
            if label is None:
                audit['option_no_exact_product_result']+=1;continue
            if label['n_way']!=n or (label['event_id'] is not None and label['event_id']!=r['event_id']):
                audit['option_result_identity_mismatch']+=1;continue
            kickoff=aware(r['kickoff']).replace(tzinfo=None).isoformat()
            if kickoff!=label['kickoff']:
                audit['option_result_kickoff_mismatch']+=1;continue
            if label['home'] is not None and (fixture['home']!=label['home'] or fixture['away']!=label['away']):
                audit['option_result_team_mismatch']+=1;continue
            slot=SEL_NAMES[(family,n)].index(r['selection'])
            price=float(r['odds'])
            if not math.isfinite(price) or price<=1:raise ValueError('invalid saved price')
            q=dict(r)
            q.update(slot=slot, date=kickoff[:10], year=int(kickoff[:4]), league=fixture['league'],
                     sport=fixture['sport'],score_contract=(chosen[r['record_hash']].get('score_forecast') or {}).get('contract'),
                     family=family,n_way=n,issued=list(key[:3]),result=label['result'],
                     refund=label['status']=='refund',hit=None if label['status']=='refund' else slot==label['winner'],
                     net_won=0 if label['status']=='refund' else int(Decimal(str(price))*STAKE)-STAKE if slot==label['winner'] else -STAKE,
                     result_source=label['source'])
            groups[(r['record_hash'],key)].append(q)
        except (KeyError,TypeError,ValueError):audit['option_invalid_contract']+=1
    rosters=[]
    for key,rows in sorted(groups.items(),key=str):
        slots={r['slot'] for r in rows}
        if len(rows)!=rows[0]['n_way'] or len(slots)!=len(rows):
            audit['incomplete_or_duplicate_market_rosters']+=1;continue
        rows.sort(key=lambda r:r['slot'])
        rosters.append(rows)
    audit['joined_complete_markets']=len(rosters)
    audit['joined_selections']=sum(len(r) for r in rosters)
    return rosters,dict(audit)


def vector(rows,field,score=False):
    if score and not all(r.get('score_probability_usable_for_model_comparison') for r in rows):return None
    if score:
        family=rows[0]['family'];contract=rows[0].get('score_contract') or {}
        expected={'sc':'goals','bs':'runs','bk':'points','vl':'sets'}.get(rows[0].get('sport'))
        if family.startswith('전반') or family=='홀짝' or expected is None or contract.get('score_unit')!=expected:return None
        if family=='언더오버' and contract.get('total_market_unit')!=contract.get('score_unit'):return None
    values=[r.get(field) for r in rows]
    if any(v is None or isinstance(v,bool) or not math.isfinite(float(v)) or not 0<=float(v)<=1 for v in values):return None
    values=list(map(float,values))
    if abs(sum(values)-1)>.002:return None
    return values


def policy_summary(rows):
    result=summarize(rows)
    q=[r for r in rows if r.get('policy_probability') is not None and not r['refund']]
    if q:
        result.update(mean_estimated_roi=sum(r['policy_probability']*r['odds']-1 for r in q)/len(q),
                      selected_outcome_brier=sum((r['policy_probability']-int(r['hit']))**2 for r in q)/len(q),
                      estimated_roi_is_not_observed_profit=True)
    return result


def compare(rosters):
    policies=defaultdict(list);paired=defaultdict(list);grouped=defaultdict(list);paired_grouped=defaultdict(list);all_choices=[];audit=Counter()
    diagnostics=defaultdict(list);daily_differences=defaultdict(lambda:defaultdict(lambda:[0,0]))
    for rows in rosters:
        all_choices.extend(rows)
        vectors={'market':vector(rows,'market_probability'),'saved_final_reference':vector(rows,'final_probability'),
                 'saved_score_shadow':vector(rows,'score_model_probability',score=True)}
        if vectors['saved_score_shadow'] is not None and vectors['market'] is not None:
            vectors['market90_score10_hypothesis']=[.9*m+.1*s for m,s in zip(vectors['market'],vectors['saved_score_shadow'])]
        else:audit['markets_without_usable_score_vector']+=1
        if vectors['saved_score_shadow'] is not None and vectors['market'] is not None:
            if not rows[0]['refund']:
                winning=next(i for i,r in enumerate(rows) if r['hit'])
                for name,probs in vectors.items():
                    if probs is None:continue
                    diagnostics[name].append((sum((p-int(i==winning))**2 for i,p in enumerate(probs)),
                                              -math.log(max(1e-15,probs[winning]))))
            reference=min(range(len(rows)),key=lambda i:(-vectors['market'][i],i))
            for name in ('saved_score_shadow','market90_score10_hypothesis'):
                probs=vectors[name];slot=min(range(len(rows)),key=lambda i:(-probs[i],i))
                day=daily_differences[name][rows[0]['date']]
                day[0]+=rows[slot]['net_won']-rows[reference]['net_won'];day[1]+=1
        for name,probs in vectors.items():
            if probs is None:continue
            slot=min(range(len(rows)),key=lambda i:(-probs[i],i));r={**rows[slot],'policy_probability':probs[slot]}
            policies[name].append(r)
            grouped[(r['league'],r['family'],name)].append(r)
            if vectors['saved_score_shadow'] is not None and vectors['market'] is not None:
                paired[name].append(r)
                paired_grouped[(r['league'],r['family'],name)].append(r)
            edge_slot=min(range(len(rows)),key=lambda i:(-(probs[i]*rows[i]['odds']-1),i))
            if probs[edge_slot]*rows[edge_slot]['odds']-1>1e-10:
                policies[name+'_positive_estimated_ev'].append({**rows[edge_slot],'policy_probability':probs[edge_slot]})
        selected=[r for r in rows if r['selected_in_snapshot'] and r['decision_action']!='withhold']
        if len(selected)==1:policies['actually_selected_in_saved_snapshot'].append(selected[0])
        elif len(selected)>1:audit['markets_with_multiple_selected_flags']+=1
    intervals={}
    for name,day_rows in daily_differences.items():
        days=sorted(day_rows);rng=random.Random(20261030);samples=[]
        for _ in range(2000):
            picked=rng.choices(days,k=len(days));num=sum(day_rows[d][0] for d in picked);den=sum(day_rows[d][1] for d in picked)*STAKE
            samples.append(num/den)
        samples.sort()
        intervals[name]=dict(kickoff_days=len(days),low=samples[50],high=samples[1950],
                             caveat='Paired date blocks on saved matched products; previously seen data, sparse dates, not proof of future profit.')
    return dict(audit=dict(audit),scope='one hypothetical single per product, not per physical event; same-event products never combined',
                policies={k:policy_summary(v) for k,v in sorted(policies.items())},
                same_score_rosters={k:policy_summary(v) for k,v in sorted(paired.items())},
                league_market={'/'.join(k):policy_summary(v) for k,v in sorted(grouped.items())},
                paired_league_market={'/'.join(k):policy_summary(v) for k,v in sorted(paired_grouped.items())},
                paired_probability_diagnostics={k:dict(n=len(v),mean_multiclass_brier=sum(x[0] for x in v)/len(v),
                        mean_log_loss=sum(x[1] for x in v)/len(v)) for k,v in diagnostics.items()},
                paired_roi_difference_intervals=intervals,
                all_joined_choices=summarize(all_choices),
                score_shadow_is_not_operating_model=True,market90_score10_is_not_market90_tipster10=True,
                current_operating_vote_policy_reproduced=False,
                model_calibration_and_historical_timestamp_independence_not_certified=True)


def load_jsonl(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(l) for l in f if l.strip()]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archives',nargs='+',type=Path,required=True)
    p.add_argument('--saved',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    snapshots=load_jsonl(args.saved/'prediction-snapshots.jsonl.gz')
    options=load_jsonl(args.saved/'all-offer-outcomes.jsonl.gz')
    extra=load_jsonl(args.saved/'issued-product-result-labels-post-20260909.jsonl.gz')
    archives=((str(path),row) for path in args.archives for row in read_source(path))
    index,result_audit=result_index(archives,extra)
    results={};joined=[]
    for freeze in (30,0):
        rosters,audit=joined_rosters(snapshots,options,index,freeze)
        results[str(freeze)]=dict(freeze_minutes=freeze,coverage=audit,**compare(rosters))
        if freeze==30:joined=[r for rows in rosters for r in rows]
    metadata=dict(protocol='saved-all-proto-outcomes-v1',stake_won=STAKE,monthly_cap_applied=False,
        result_audit=result_audit,experiments=results,
        saved_input_sha256={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in
            [args.saved/'prediction-snapshots.jsonl.gz',args.saved/'all-offer-outcomes.jsonl.gz',args.saved/'issued-product-result-labels-post-20260909.jsonl.gz']})
    (args.output/'saved-model-results.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    body=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in joined).encode()
    (args.output/'joined-saved-choices.jsonl.gz').write_bytes(gzip.compress(body,mtime=0))
    print(json.dumps({k:v['coverage'] for k,v in results.items()},ensure_ascii=False))


if __name__=='__main__':main()
