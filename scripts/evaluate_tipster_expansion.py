"""Offline, exact-fixture tipster audit; no production storage or network calls.

Input rows are normalized by collection adapters. Official scores, quoted prices,
and archived Proto offers are separate evidence. Missing evidence stays missing.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from devig import market_probabilities

KST = timezone(timedelta(hours=9))


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite number')
    return value


def decimal_price(value, american=False):
    x = number(value)
    if american:
        if abs(x) < 100:
            raise ValueError('invalid American price')
        x = 1 + (x/100 if x > 0 else 100/abs(x))
    if x <= 1:
        raise ValueError('invalid decimal price')
    return x


def stamp(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.utcoffset() is None:
        raise ValueError('timezone required')
    return t


def team_key(value):
    return re.sub(r'[\s\W_]+', '', str(value).casefold())


def settle(row):
    """Return win/loss/push, using independently joined integer final scores."""
    if row.get('result_evidence') != 'official_exact_fixture' or not row.get('result_source_url'):
        raise ValueError('unverified result')
    h, a = row.get('home_score'), row.get('away_score')
    if type(h) is not int or type(a) is not int or min(h, a) < 0:
        raise ValueError('invalid scores')
    market, side = row['market'], row['side']
    if market == 'moneyline':
        if row['league'] not in {'K리그1', 'K리그2'} and row.get('period') != 'full_game_including_overtime':
            raise ValueError('unknown settlement period')
        if side == 'draw':
            if row['league'] not in {'K리그1', 'K리그2'}:
                raise ValueError('unsupported draw market')
            return 'win' if h == a else 'loss'
        if side not in {'home', 'away'}:
            raise ValueError('unknown side')
        if h == a:
            return 'loss' if row['league'] in {'K리그1', 'K리그2'} else 'push'
        margin = h-a if side == 'home' else a-h
    elif market in {'spread', 'total'}:
        line = number(row['line'])
        if not (line*2).is_integer():
            raise ValueError('split Asian line excluded')
        if row.get('period') != 'full_game_including_overtime':
            raise ValueError('unknown settlement period')
        if market == 'spread' and side in {'home', 'away'}:
            margin = (h-a if side == 'home' else a-h) + line
        elif market == 'total' and side in {'over', 'under'}:
            margin = (h+a-line) * (1 if side == 'over' else -1)
        else:
            raise ValueError('unknown market side')
    else:
        raise ValueError('unsupported market')
    return 'win' if margin > 0 else 'loss' if margin < 0 else 'push'


def candidate_key(r):
    return (r['source'], r.get('author') or r['source'], r['fixture_id'], r['market'], r.get('line'))


def deduplicate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[candidate_key(row)].append(row)
    accepted, excluded = [], []
    for _, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        signatures = {(r['side'], r.get('reported_decimal_odds')) for r in group}
        if len(signatures) > 1:
            excluded.extend(dict(row=r, reason='conflicting_pick_or_quote') for r in group)
            continue
        ordered = sorted(group, key=lambda r: (r.get('source_url', ''), r.get('observed_at', '')))
        accepted.append(ordered[0])
        excluded.extend(dict(row=r, reason='duplicate') for r in ordered[1:])
    return accepted, excluded


def quote_profit(row):
    quality = row.get('odds_time_quality')
    if quality not in {'original_recommendation_claim', 'first_observed_pregame',
                       'source_reported_historical_recommendation_price_unverified',
                       'retrospective_listed_price_claim','archived_proto_price_without_purchase_time'}:
        return None
    try:
        price = decimal_price(row.get('reported_decimal_odds'))
    except (ValueError, TypeError):
        return None
    return price-1 if row['settlement'] == 'win' else -1 if row['settlement'] == 'loss' else 0.


def summary(rows):
    c = Counter(r['settlement'] for r in rows)
    profits = [p for r in rows if (p := quote_profit(r)) is not None]
    return dict(picks=len(rows), unique_fixtures=len({r['fixture_id'] for r in rows}),
                wins=c['win'], losses=c['loss'], pushes=c['push'],
                hit_rate=c['win']/(c['win']+c['loss']) if c['win']+c['loss'] else None,
                priced_picks=len(profits), quoted_unit_net=sum(profits) if profits else None,
                quoted_roi=sum(profits)/len(profits) if profits else None,
                certified_pregame_picks=sum(certified(r) for r in rows),
                priced_quality_counts=dict(Counter(r.get('odds_time_quality') for r in rows if quote_profit(r) is not None)),
                dates=sorted({r['match_date'] for r in rows}))


def certified(row):
    try:
        return (row.get('cutoff_proven') is True and bool(row.get('independent_pregame_evidence_url'))
                and stamp(row['observed_at']) <= stamp(row['kickoff'])-timedelta(minutes=30))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def capped_replay(rows):
    """Daily reservation/next-day settlement proxy, one shared monthly budget."""
    days = defaultdict(list)
    for row in rows:
        if quote_profit(row) is not None:
            days[row['match_date']].append(row)
    monthly, ledger = defaultdict(float), []
    curve, peak, drawdown = 0., 0., 0.
    for day, batch in sorted(days.items()):
        month = day[:7]
        chosen, seen = [], set()
        for row in sorted(batch, key=lambda r: (r.get('kickoff') or day, str(candidate_key(r)))):
            # Never multiply stake merely because a fixture has multiple authors/markets.
            if row['fixture_id'] in seen:
                continue
            seen.add(row['fixture_id'])
            if monthly[month] - 10000*(len(chosen)+1) < -50000-1e-8:
                continue
            chosen.append(row)
        net = sum(10000*quote_profit(r) for r in chosen)
        monthly[month] += net
        curve += net; peak = max(peak, curve); drawdown = max(drawdown, peak-curve)
        for r in chosen:
            ledger.append(dict(source=r['source'], fixture_id=r['fixture_id'], date=day,
                               profit_krw=round(10000*quote_profit(r), 4)))
    assert all(x >= -50000-1e-6 for x in monthly.values())
    return dict(bets=len(ledger), profit_krw=round(sum(monthly.values()), 4),
                roi=sum(monthly.values())/(10000*len(ledger)) if ledger else None,
                monthly_net=dict(monthly), max_daily_drawdown_krw=round(drawdown, 4),
                settlement_timing='same-day exposure reserved; next-day proxy; bet-month accounting',
                ledger=ledger)


def market_comparison(row):
    """Fixed market proxy vs new vote vs market90+vote10; not frozen operating model."""
    if row['market'] != 'moneyline' or row.get('proto_offer_evidence') != 'archived_exact_fixture':
        return None
    prices = row.get('proto_odds')
    order = ['home', 'draw', 'away'] if row['league'] in {'K리그1', 'K리그2'} else ['home', 'away']
    if not isinstance(prices, list) or len(prices) != len(order) or row['side'] not in order:
        return None
    prices = [decimal_price(x) for x in prices]
    p = market_probabilities(prices)
    v = order.index(row['side'])
    hybrid = [round(.9*x + .1*(i == v), 4) for i, x in enumerate(p)]
    picks = dict(archived_market_reference=max(range(len(p)), key=p.__getitem__),
                 tipster=v, market90_new_tipster10=max(range(len(p)), key=hybrid.__getitem__))
    winner = 'draw' if row['home_score'] == row['away_score'] else 'home' if row['home_score'] > row['away_score'] else 'away'
    returns = {k: (0. if winner == 'draw' and len(order)==2 else prices[i]-1 if order[i]==winner else -1.) for k, i in picks.items()}
    return dict(fixture_id=row['fixture_id'], source=row['source'], author=row.get('author'),
                match_date=row['match_date'], kickoff=row.get('kickoff'), league=row['league'], winner=winner,
                prices=prices, picks={k:order[v] for k,v in picks.items()}, unit_net=returns,
                changed_from_market=picks['archived_market_reference'] != picks['market90_new_tipster10'])


def evaluate(rows):
    # Include unresolved candidates in the audit; never treat their missing results as losses.
    accepted, excluded = [], []
    for row in rows:
        try:
            if not row.get('fixture_id'):
                raise ValueError('unmatched fixture')
            row = dict(row, settlement=settle(row))
            accepted.append(row)
        except (ValueError, KeyError, TypeError) as exc:
            excluded.append(dict(row=row, reason=str(exc)))
    accepted, duplicates = deduplicate(accepted); excluded += duplicates
    groups = {}
    for field in ('source', 'league', 'author'):
        buckets = defaultdict(list)
        for row in accepted:
            key = row.get(field) or row['source']
            if field == 'author':
                key = row['source'] + ' / ' + key
            buckets[key].append(row)
        groups[field] = {k:dict(**summary(v), **({'monthly_capped':capped_replay(v)} if field!='league' else {}))
                         for k,v in sorted(buckets.items())}
    comparisons = []
    for row in accepted:
        try:
            value = market_comparison(row)
            if value is not None:
                comparisons.append(value)
        except (ValueError, TypeError):
            pass
    paired = {}
    for source in sorted({x['source'] for x in comparisons}):
        batch = [r for r in comparisons if r['source']==source]
        capped={}
        hit_rates={}
        for arm in batch[0]['unit_net']:
            simulated=[]
            for r in batch:
                side=r['picks'][arm]
                settlement=('push' if r['winner']=='draw' and len(r['prices'])==2 else 'win' if side==r['winner'] else 'loss')
                order=['home','draw','away'] if len(r['prices'])==3 else ['home','away']
                simulated.append(dict(r, side=side, market='moneyline',settlement=settlement,
                                      reported_decimal_odds=r['prices'][order.index(side)],
                                      odds_time_quality='archived_proto_price_without_purchase_time'))
            capped[arm]=capped_replay(simulated)
            c=Counter(r['settlement'] for r in simulated)
            hit_rates[arm]=c['win']/(c['win']+c['loss']) if c['win']+c['loss'] else None
        paired[source] = dict(n=len(batch), changed_picks=sum(r['changed_from_market'] for r in batch),
                              hit_rate=hit_rates, monthly_capped=capped,
                              roi={arm:sum(r['unit_net'][arm] for r in batch)/len(batch) for arm in batch[0]['unit_net']})
    return dict(scope='exploratory retrospectively recovered picks; no automatic promotion',
                input_candidates=len(rows), settled=summary(accepted), groups=groups,
                excluded_counts=dict(Counter(r['reason'] for r in excluded)), excluded=excluded,
                accepted=accepted, paired_market_proxy=paired, paired_ledger=comparisons,
                frozen_operating_incremental_roi=None,
                limitation='No frozen operating predictions on identical events/quotes; market proxy is not existing model.',
                monthly_loss_limit_krw=50000, production_changes=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Normalized JSON array or JSONL')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes(); text = raw.decode('utf-8-sig')
    rows = json.loads(text) if text.lstrip().startswith('[') else [json.loads(x) for x in text.splitlines() if x.strip()]
    result = evaluate(rows)
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k in {'input_candidates','settled','excluded_counts','paired_market_proxy'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
