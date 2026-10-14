"""Offline paper comparator. No claim that saved probabilities are calibrated.

Freeze two alternatives: existing pick versus maximum saved p*odds-1 within
the same 1.5<=odds<2.2 range. Same eligible events and one unit per bet.
Only jointly recorded complete rosters can form a two-event ticket.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import math

from recommendation_history import selection_key
from per_event_shadow import event_key


def timestamp(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return t if t.utcoffset() is not None else None
    except (TypeError,ValueError):
        return None


def valid_pick(row):
    try:
        p,o = float(row['predicted_hit_prob']),float(row['odds'])
        return math.isfinite(p) and math.isfinite(o) and 0<p<1 and 1.5<=o<2.2
    except (KeyError,TypeError,ValueError):
        return False


def payout(pick):
    if pick.get('result_source') != 'official':
        return None
    return (float(pick['odds']) if pick.get('result')=='hit' else
            0.0 if pick.get('result')=='miss' else 1.0 if pick.get('result')=='void' else None)


def summarize(tickets):
    profits = []
    for ticket in tickets:
        returns = [payout(p) for p in ticket]
        if all(v is not None for v in returns):
            profits.append(math.prod(returns)-1)
    return {'selected':len(tickets),'settled':len(profits),'pending':len(tickets)-len(profits),
            'profit_units':sum(profits),'roi':sum(profits)/len(profits) if profits else None}


def compare_tickets(base, challenger):
    complete = [(a,b) for a,b in zip(base,challenger) if all(payout(p) is not None for p in a+b)]
    a,b = summarize([a for a,b in complete]), summarize([b for a,b in complete])
    return {'baseline':summarize(base),'challenger':summarize(challenger),
            'common_settled':len(complete),
            'common_roi_difference':b['roi']-a['roi'] if complete else None}


def replay(payload, asof):
    groups = defaultdict(list)
    exclusions = Counter()
    identities = set()
    for record in (payload.get('per_event_shadow',{}).get('records') or {}).values():
        roster = record.get('candidate_roster') or []
        baseline = record.get('baseline') or {}
        times = [timestamp(record.get(k)) for k in ('recorded_at','generated_at','source_generated_at','live_odds_at')]
        kickoff = timestamp(record.get('kickoff_at'))
        if record.get('roster_status') != 'complete' or not roster or not record.get('observation_batch_id'):
            exclusions['missing_complete_roster'] += 1
            continue
        if (not all(times) or not kickoff or times[0]>=kickoff-timedelta(minutes=30)
                or any(not timedelta(0)<=times[0]-t<=timedelta(minutes=15) for t in times[1:])):
            exclusions['invalid_time'] += 1
            continue
        if (not record.get('id') or record['id'] in identities
                or len({selection_key(r) for r in roster})!=len(roster)
                or any(event_key(r)!=record['id'] for r in roster)):
            exclusions['duplicate_or_missing_identity'] += 1
            continue
        # Baseline must really be offered at the same price in this roster.
        matches = [r for r in roster if selection_key(r)==selection_key(baseline)
                   and r.get('odds')==baseline.get('odds')]
        if len(matches)!=1 or not valid_pick(matches[0]):
            exclusions['baseline_not_in_price_range_or_roster'] += 1
            continue
        eligible = [r for r in roster if valid_pick(r)]
        selected = min(eligible,key=lambda r: (-(float(r['predicted_hit_prob'])*float(r['odds'])-1),
                                                -float(r['predicted_hit_prob']),selection_key(r)))
        identities.add(record['id'])
        baseline_pick = dict(matches[0])
        selected = dict(selected)
        for pick in (baseline_pick,selected):
            settled_at = timestamp(pick.get('settled_at'))
            if kickoff > asof or (settled_at and settled_at > asof):
                pick['result'] = 'pending'
        # Batch ID plus timestamps: same ID alone cannot certify simultaneity.
        batch = (record['observation_batch_id'],*times)
        groups[batch].append((kickoff,record['id'],baseline_pick,selected))
    rows = [r for group in groups.values() for r in group]
    pairs = []
    for group in groups.values():
        ordered = sorted(group,key=lambda r:(r[0],r[1]))
        pairs.extend((ordered[i],ordered[i+1]) for i in range(0,len(ordered)-1,2))
    return {'policy':'paper-max-saved-ev-price150-220-v1',
            'eligible_same_events':len(rows),'different_picks':sum(selection_key(r[2])!=selection_key(r[3]) for r in rows),
            'singles':compare_tickets([[r[2]] for r in rows],[[r[3]] for r in rows]),
            'pairs':compare_tickets([[a[2],b[2]] for a,b in pairs],[[a[3],b[3]] for a,b in pairs]),
            'exclusions':dict(exclusions),
            'promotion':'NOT_MET: paper comparator only, no validated advantage',
            'pair_rule':'earliest kickoff/id; disjoint two-event pairs inside exact first-observation batch; no forced cross-batch pairing'}
