"""Offline descriptive audit. Does not manufacture a prospective combo backtest."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from recommendation_history import KST, number, settle_history, stamp


def valid(entry, now):
    kickoff, published, recorded = (stamp(entry.get(k)) for k in ('kickoff_at','published_at','recorded_at'))
    return (entry.get('recommended') is True and entry.get('id') and kickoff and published and recorded
            and max(published, recorded) < kickoff-timedelta(minutes=30)
            and max(published, recorded) <= now and 0 < number(entry.get('probability')) < 1
            and number(entry.get('odds')) > 1)


def selected(entry, threshold):
    return threshold is None or (number(entry['probability']) >= threshold and number(entry['odds']) >= 1.5)


def day(entry):
    return stamp(entry['kickoff_at']).astimezone(KST).date().isoformat()


def profit(entry):
    return number(entry['odds'])-1 if entry['result'] == 'hit' else -1


def percentile(values, fraction):
    values = sorted(values)
    pos = (len(values)-1)*fraction
    lo = int(pos)
    return values[lo] + (values[min(lo+1,len(values)-1)]-values[lo])*(pos-lo)


def paired_interval(rows, threshold, repeats=10000):
    """Resample KST dates together; no independence assumption between same-day bets."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[day(row)].append(row)
    aggregates = []
    for key in sorted(grouped):
        base = grouped[key]
        filtered = [r for r in base if selected(r, threshold)]
        aggregates.append((sum(map(profit,base)),len(base),sum(map(profit,filtered)),len(filtered)))
    if not aggregates:
        return None
    rng = random.Random(20260925)
    roi_delta, budget_delta = [], []
    for _ in range(repeats):
        draw = rng.choices(aggregates,k=len(aggregates))
        base_p, base_n, filt_p, filt_n = (sum(r[i] for r in draw) for i in range(4))
        if filt_n:
            roi_delta.append(filt_p/filt_n-base_p/base_n)
        budget_delta.append(sum((p/n if n else 0)-bp/bn for bp,bn,p,n in draw)/len(draw))
    return {'method':'paired KST-day cluster bootstrap; exploratory, unadjusted for threshold selection',
            'replicates':repeats,'seed':20260925,'dates':len(aggregates),
            'roi_difference_95pct': [percentile(roi_delta,q) for q in (.025,.975)] if roi_delta else None,
            'equal_daily_budget_difference_95pct':[percentile(budget_delta,q) for q in (.025,.975)]}


def audit(payload, prices, now, policy_cutoff):
    history = deepcopy(payload.get('recommendation_history') or {})
    rows = list({r['id']:r for r in history.values() if valid(r,now)}.values())
    settle_history({r['id']:r for r in rows},prices,now)
    for r in rows:
        if r.get('result_source') != 'official' or stamp(r['kickoff_at']) > now:
            r['result'] = 'pending'
    settled = [r for r in rows if r.get('result') in ('hit','miss')]
    dates = sorted({day(r) for r in settled})
    cohorts = {}
    for label, threshold in (('all',None),('p58_o150',.58),('p60_o150',.60)):
        group = [r for r in settled if selected(r,threshold)]
        pnl = sum(map(profit,group))
        daily = []
        for date in dates:
            d = [r for r in group if day(r)==date]
            daily.append(sum(map(profit,d))/len(d) if d else 0)
        balance = peak = dd = 0
        for value in daily:
            balance += value
            peak = max(peak,balance)
            dd = max(dd,peak-balance)
        cohorts[label] = {'settled':len(group),'hits':sum(r['result']=='hit' for r in group),
                          'unit_stake_profit':pnl,'roi':pnl/len(group) if group else None,
                          'selected_fraction':len(group)/len(settled) if settled else None,
                          'equal_daily_budget_profit':sum(daily),
                          'equal_daily_budget_return':sum(daily)/len(dates) if dates else None,
                          'equal_daily_budget_drawdown':dd,
                          'active_dates':sum(any(day(r)==date for r in group) for date in dates),
                          'pending':sum(selected(r,threshold) and r.get('result') not in ('hit','miss','void') for r in rows),
                          'void':sum(selected(r,threshold) and r.get('result')=='void' for r in rows),
                          'saved_probability_mean_ev':sum(r['probability']*r['odds']-1 for r in group)/len(group) if group else None,
                          'interval':paired_interval(settled,threshold) if threshold else None}
    combos = {}
    for label in ('daily','six_hour'):
        grouped = defaultdict(list)
        for row in rows:
            if selected(row,.60):
                slot = stamp(row['kickoff_at']).astimezone(KST).hour//6
                grouped[(day(row),slot if label=='six_hour' else 0)].append(row)
        counts = Counter()
        for group in grouped.values():
            counts['groups'] += 1
            ranked = sorted(group,key=lambda r:(-r['probability'],r['odds'],r['id']))
            if len(ranked)<2:
                counts['insufficient'] += 1
                continue
            legs = ranked[:2]
            counts['two_or_more'] += 1
            cutoff = min(stamp(r['kickoff_at']) for r in legs)-timedelta(minutes=30)
            if any(max(stamp(r['published_at']),stamp(r['recorded_at'])) >= cutoff for r in legs):
                counts['not_known_together_by_T30'] += 1
                continue
            counts['known_together_by_T30'] += 1
            if any(cutoff-min(stamp(r['published_at']),stamp(r['recorded_at'])) > timedelta(minutes=15) for r in legs):
                counts['snapshot_older_than_15m'] += 1
                continue
            counts['time_checks_pass'] += 1
            if all(r.get('result') in ('hit','miss','void') for r in legs):
                counts['time_checks_pass_and_settled'] += 1
        combos[label] = dict(counts)
    return {'artifact_generated_at':payload.get('generated_at'),'odds_generated_at':prices.get('generated_at'),
            'audit_at':now.isoformat(),'policy_cutoff':policy_cutoff.isoformat(),
            'raw_archive':len(history),'valid_recommended':len(rows),'settled_dates':dates,
            'cohorts':cohorts,'combo_temporal_feasibility_only':combos,
            'holdout_settled_after_policy_cutoff':sum(stamp(r['recorded_at'])>=policy_cutoff for r in settled),
            'holdout_p60_settled_after_policy_cutoff':sum(stamp(r['recorded_at'])>=policy_cutoff and selected(r,.60) for r in settled),
            'not_a_v2_replay':'No historical simultaneous candidate rosters, source freshness or frozen slot budgets. Equal-day figures use settled singles ex post; not deployable backtest.',
            'promotion_gate':'NOT_MET: threshold chosen on these data; independent prospective cohort required'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('recommendations',type=Path)
    parser.add_argument('odds',type=Path)
    parser.add_argument('--policy-cutoff',required=True)
    args = parser.parse_args()
    result = audit(json.loads(args.recommendations.read_text(encoding='utf-8-sig')),
                   json.loads(args.odds.read_text(encoding='utf-8-sig')),datetime.now(timezone.utc),stamp(args.policy_cutoff))
    result['sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.recommendations,args.odds)}
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
