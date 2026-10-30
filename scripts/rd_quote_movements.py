"""Outcome-blind earlier quote movements on the exact issued moneyline.

Saved clocks are internal historical claims, not publisher-certified receipt.
Repeated model revisions with one cached quote do not become price history.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import math
from pathlib import Path

import pregame_rd_compare as m
from rd_asof_evidence import timestamp


def project(frame):
    return {**{k:frame.get(k)for k in ('snapshot_id','event_id','league','home','away','kickoff',
                                    'as_of','captured_at','quote_observed_at','market_observed_at')},
            'choices':[{k:c.get(k)for k in ('market','selection','issue_year','round','game_no','odds')}
                       for c in frame['choices'] if c.get('market')in ('승패','승무패')]}


def quotes(frame, case):
    decision=timestamp(case['decision_at'])
    quoted=timestamp(frame['quote_observed_at']);asof=timestamp(frame['as_of']);captured=timestamp(frame['captured_at'])
    if not quoted<=asof<=captured<decision:
        return None,'inconsistent_or_post_decision_quote_clocks'
    choices=[c for c in frame['choices'] if c['market']in ('승패','승무패')
             and [c.get('issue_year'),c.get('round')]==case['issue']
             and str(c.get('game_no'))==str(case['game_no'])]
    sides=[m.ALIASES.get(c['selection'])for c in choices]
    if len(choices)!=len(case['sides']) or set(sides)!=set(case['sides']):
        return None,'missing_or_ambiguous_exact_issued_product'
    prices=[choices[sides.index(s)]['odds']for s in case['sides']]
    if not all(isinstance(v,(int,float))and not isinstance(v,bool)and math.isfinite(v)and v>=1 for v in prices):
        return None,'invalid_price'
    return dict(quote_at=quoted.isoformat(),captured_at=captured.isoformat(),prices=prices,
                snapshot_id=frame['snapshot_id']),None


def extract(cases, frames, source_hash):
    index=defaultdict(list)
    audit=Counter()
    for f in frames:
        if not all(isinstance(f.get(k),str) and f[k].strip() for k in ('league','home','away','kickoff')):
            audit['source_frame_missing_fixture']+=1;continue
        try:index[m.physical_key(f)].append(f)
        except (KeyError,TypeError,ValueError):audit['source_frame_invalid_fixture_time']+=1;continue
    output=[]
    for case in cases:
        history=[]
        for frame in index.get(m.physical_key(case),[]):
            try:q,reason=quotes(frame,case)
            except (KeyError,TypeError,ValueError):q,reason=None,'missing_or_naive_quote_time'
            if q:history.append(q)
            else:audit[reason]+=1
        by_time=defaultdict(list)
        for q in history:by_time[q['quote_at']].append(q)
        if any(len({tuple(x['prices'])for x in group})>1 for group in by_time.values()):
            audit['conflicting_same_time_quote_payload']+=1;continue
        history=sorted([min(group,key=lambda r:r['captured_at'])for group in by_time.values()],key=lambda q:q['quote_at'])
        if len(history)<2:
            audit['fewer_than_two_distinct_quote_times']+=1;continue
        first,last=history[0],history[-1]
        if timestamp(case['decision_at'])-timestamp(last['quote_at'])>timedelta(minutes=120):
            audit['stale_final_quote_over120min']+=1;continue
        if any(abs(a-b)>1e-8 for a,b in zip(last['prices'],case['odds'])):
            audit['final_quote_does_not_match_frozen_case_price']+=1;continue
        p0=[1/v for v in first['prices']];p1=[1/v for v in last['prices']]
        p0=[v/sum(p0)for v in p0];p1=[v/sum(p1)for v in p1]
        features={}
        for i,side in enumerate(case['sides']):
            features[side+'_implied_change_pp']=100*(p1[i]-p0[i])
            features[side+'_log_price_ratio']=math.log(last['prices'][i]/first['prices'][i])
        changed=sum(a['prices']!=b['prices']for a,b in zip(history,history[1:]))
        features.update(changed_quote_count=changed,history_hours=(timestamp(last['quote_at'])-timestamp(first['quote_at'])).total_seconds()/3600)
        evidence=dict(max_fact_time=last['quote_at'],observed_at=max((q['captured_at']for q in history),key=timestamp),
                      source_sha256=[source_hash],availability_tier='retrospective_lagged',
                      note='Internal saved quote/capture clocks; exact product/price match, no first-receipt certification')
        output.append({**{k:case[k]for k in ('cohort','event_id','league','kickoff','decision_at')},
                       'features':features,'feature_evidence':{n:dict(evidence)for n in features},
                       'source_ids':[q['snapshot_id']for q in history]})
        audit['accepted_events']+=1;audit['events_with_actual_price_change']+=changed>0
    return output,dict(audit)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--cases',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    frames=[project(f)for f in m.load(args.source)]
    projection=args.output/'quote-inputs-NO-LABELS.jsonl.gz';m.save(projection,frames,True)
    sha=hashlib.sha256(projection.read_bytes()).hexdigest();rows,audit=extract(m.load(args.cases),frames,sha)
    m.save(args.output/'features.jsonl.gz',rows,True)
    m.save(args.output/'manifest.json',dict(source=str(args.source),source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
           projection_sha256=sha,source_scope='internal_saved_time_only_not_external_execution_certification',
           raw_frames=len(frames),output_events=len(rows),audit=audit,feature_definitions='side normalized inverse-price change_pp, log_price_ratio, changed_quote_count, history_hours',
           production_changed=False))
    print(m.__name__,len(rows),audit)


if __name__=='__main__':main()
