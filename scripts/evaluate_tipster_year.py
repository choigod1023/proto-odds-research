"""Fixed multi-author historical audit. Offline; never promotes a model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from scripts.evaluate_tipster_expansion import (
    capped_replay, certified, deduplicate, market_comparison, quote_profit, settle,
)

START, END = '2025-10-28', '2026-10-27'
MONTHS = ['2025-10', '2025-11', '2025-12'] + [f'2026-{i:02}' for i in range(1, 11)]
MIDPOINT = '2026-04-28'
ARMS = ('archived_market_reference', 'tipster', 'market90_new_tipster10')


def week(day):
    d = date.fromisoformat(day)
    return (d-timedelta(days=d.weekday())).isoformat()


def interval(days, numerators, denominators, repetitions=2000):
    """Resample observed calendar weeks as clusters; no IID claim for authors."""
    sums = defaultdict(lambda: [0., 0.])
    for day, num, den in zip(days, numerators, denominators):
        key = week(day)
        sums[key][0] += float(num)
        sums[key][1] += float(den)
    if len(sums) < 2:
        return dict(low=None, high=None, observed_weeks=len(sums), reason='fewer_than_two_observed_weeks')
    data = np.array(list(sums.values()), dtype=float)
    rng = np.random.default_rng(20261028)
    samples = rng.integers(len(data), size=(repetitions, len(data)))
    totals = data[samples].sum(axis=1)
    ratios = totals[totals[:, 1] > 0, 0] / totals[totals[:, 1] > 0, 1]
    lo, hi = np.quantile(ratios, [.025, .975])
    return dict(low=float(lo), high=float(hi), observed_weeks=len(sums), repetitions=repetitions,
                method='observed-calendar-week cluster percentile bootstrap; exploratory, not bias corrected')


def coverage(rows):
    dates = sorted({r['match_date'] for r in rows})
    return dict(picks=len(rows), unique_fixtures=len({r['fixture_id'] for r in rows}),
        authors=len({(r['source'], r.get('author') or '<unknown>') for r in rows}),
        known_authors=len({(r['source'], r['author']) for r in rows if r.get('author')}),
        dates=len(dates), observed_weeks=len({week(d) for d in dates}),
        first=dates[0] if dates else None, last=dates[-1] if dates else None,
        calendar_span_days=(date.fromisoformat(dates[-1])-date.fromisoformat(dates[0])).days+1 if dates else 0,
        monthly_fixtures={m:len({r['fixture_id'] for r in rows if r['match_date'].startswith(m)}) for m in MONTHS},
        certified_pregame_picks=sum(certified(r) for r in rows))


def describe(rows):
    wins = [int(r['settlement']=='win') for r in rows]
    nonpush = [int(r['settlement']!='push') for r in rows]
    priced = [r for r in rows if quote_profit(r) is not None]
    profits = [quote_profit(r) for r in priced]
    c = Counter(r['settlement'] for r in rows)
    sensitivity = None
    if profits:
        # Remove whole fixture contributions, including all authors on it.
        groups = defaultdict(list)
        for r in priced:
            groups[r['fixture_id']].append(quote_profit(r))
        removed = set(sorted(groups, key=lambda k:sum(groups[k]), reverse=True)[:max(1, math.ceil(len(groups)*.01))])
        keep = [r for r in priced if r['fixture_id'] not in removed]
        sensitivity = dict(removed_fixtures=len(removed), remaining_picks=len(keep),
            remaining_roi=sum(quote_profit(r) for r in keep)/len(keep) if keep else None)
    return dict(**coverage(rows), wins=c['win'], losses=c['loss'], pushes=c['push'],
        pick_hit_rate=sum(wins)/sum(nonpush) if sum(nonpush) else None,
        hit_rate_interval=interval([r['match_date'] for r in rows], wins, nonpush),
        priced_picks=len(priced), quoted_roi=sum(profits)/len(profits) if profits else None,
        roi_interval=interval([r['match_date'] for r in priced], profits, [1]*len(priced)),
        without_top_one_percent_fixtures=sensitivity,
        monthly_capped=capped_replay(priced),
        monthly={m:dict(picks=sum(r['match_date'].startswith(m) for r in rows),
            priced_picks=sum(r['match_date'].startswith(m) for r in priced),
            roi=(sum(quote_profit(r) for r in priced if r['match_date'].startswith(m)) /
                 sum(r['match_date'].startswith(m) for r in priced))
                 if any(r['match_date'].startswith(m) for r in priced) else None) for m in MONTHS})


def consensus(rows):
    """One game, one vote per known source/author; tied plurality abstains."""
    buckets = defaultdict(list)
    for r in rows:
        if r['market']=='moneyline':
            buckets[(r['league'], r['fixture_id'])].append(r)
    outputs, rejected = [], Counter()
    for key, batch in sorted(buckets.items()):
        # Missing author cannot manufacture a distinct person from each URL.
        votes = defaultdict(set)
        for r in batch:
            votes[(r['source'], r.get('author') or '<unknown>')].add(r['side'])
        directions = [next(iter(v)) for v in votes.values() if len(v)==1]
        if not directions:
            rejected['all_authors_conflict'] += 1
            continue
        counts = Counter(directions)
        best = max(counts.values())
        if sum(v==best for v in counts.values()) != 1:
            rejected['tied_plurality'] += 1
            continue
        f = dict(batch[0])
        outcome = {(r['home_score'],r['away_score'],r['match_date']) for r in batch}
        if len(outcome)!=1:
            rejected['inconsistent_fixture_result'] += 1
            continue
        prices = {tuple(r['proto_odds']) for r in batch if r.get('proto_offer_evidence')=='archived_exact_fixture'}
        if len(prices)!=1:
            rejected['missing_or_conflicting_full_proto_prices'] += 1
            continue
        f.update(source='pooled_author_consensus', author='equal_votes', side=counts.most_common(1)[0][0],
            proto_odds=list(next(iter(prices))), proto_offer_evidence='archived_exact_fixture')
        comparison = market_comparison(f)
        if comparison is None:
            rejected['unsupported_market'] += 1
            continue
        order = ['home','draw','away'] if len(f['proto_odds'])==3 else ['home','away']
        from devig import market_probabilities
        p = market_probabilities(f['proto_odds'])
        shares = [counts[s]/len(directions) for s in order]
        hybrid = [round(.9*a+.1*b,4) for a,b in zip(p,shares)]
        side = order[max(range(len(order)), key=hybrid.__getitem__)]
        winner = comparison['winner']
        comparison['picks']['market90_new_tipster10'] = side
        comparison['unit_net']['market90_new_tipster10'] = (
            0. if winner=='draw' and len(order)==2 else f['proto_odds'][order.index(side)]-1 if side==winner else -1.)
        comparison.update(voters=len(directions), known_voters=sum(bool(r.get('author')) for r in batch), vote_counts=dict(counts))
        outputs.append(comparison)
    return outputs, dict(rejected)


def paired_describe(rows):
    stats = {}
    for arm in ARMS:
        normalized = []
        for r in rows:
            side = r['picks'][arm]
            order = ['home','draw','away'] if len(r['prices'])==3 else ['home','away']
            normalized.append(dict(r, source='pooled', author='equal_votes', market='moneyline', side=side,
                settlement='push' if r['winner']=='draw' and len(order)==2 else 'win' if side==r['winner'] else 'loss',
                reported_decimal_odds=r['prices'][order.index(side)], odds_time_quality='archived_proto_price_without_purchase_time'))
        stats[arm] = describe(normalized)
    diffs = [r['unit_net'][ARMS[2]]-r['unit_net'][ARMS[0]] for r in rows]
    return dict(n=len(rows), changed_picks=sum(r['picks'][ARMS[2]]!=r['picks'][ARMS[0]] for r in rows),
        arms=stats, hybrid_minus_market_roi=sum(diffs)/len(diffs) if diffs else None,
        difference_interval=interval([r['match_date'] for r in rows], diffs, [1]*len(diffs)))


def evaluate(rows):
    accepted, rejected = [], Counter()
    for raw in rows:
        r = dict(raw)
        if not START <= r.get('match_date','') <= END:
            rejected['outside_fixed_window'] += 1
            continue
        try:
            if not r.get('fixture_id'):
                raise ValueError('unmatched fixture')
            r['settlement'] = settle(r)
            accepted.append(r)
        except (ValueError, TypeError, KeyError) as exc:
            rejected[str(exc)] += 1
    accepted, duplicates = deduplicate(accepted)
    rejected.update(r['reason'] for r in duplicates)
    pooled, pooled_rejected = consensus(accepted)
    sources = {s:describe([r for r in accepted if r['source']==s]) for s in sorted({r['source'] for r in accepted})}
    leagues = {s:describe([r for r in accepted if r['league']==s]) for s in sorted({r['league'] for r in accepted})}
    return dict(requested_window=[START, END], collected_candidates=len(rows), accepted=accepted,
        coverage=coverage(accepted), excluded=dict(rejected), by_source=sources, by_league=leagues,
        paired_author_consensus=paired_describe(pooled), consensus_excluded=pooled_rejected,
        paired_by_league={s:paired_describe([r for r in pooled if r['league']==s]) for s in sorted({r['league'] for r in pooled})},
        paired_multiple_authors=paired_describe([r for r in pooled if r['voters']>=2]),
        paired_halves={name:paired_describe([r for r in pooled if (r['match_date']<MIDPOINT)==early])
            for name,early in [('2025-10-28_to_2026-04-27',True),('2026-04-28_to_2026-10-27',False)]},
        paired_ledger=pooled, frozen_operating_model_comparison=None,
        monthly_loss_limit_krw=50000, production_changes=False,
        caveat='Retrospective available-record sample. Cluster intervals cannot remove missing/deleted post or author selection bias.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    raw = a.input.read_bytes()
    rows = json.loads(raw.decode('utf-8-sig'))
    result = evaluate(rows)
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(dict(coverage=result['coverage'], excluded=result['excluded'], paired=result['paired_author_consensus']['n']), ensure_ascii=False))
