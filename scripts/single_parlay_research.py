"""Outcome-blind, same-checkpoint and equal-budget historical ticket experiment.

Standalone consumer of the frozen normalized event frames. Never imports runtime,
opens a network connection or writes production configuration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random

KST = timezone(timedelta(hours=9))
BUDGET = 12000
STRATEGIES = {
    'single_control': (1, 1.01, None, True),
    'single_long_3_5': (1, 3., 5., False),
    'single_long_5_10': (1, 5., 10., False),
    'low_single': (1, 1.10, 1.50, True),
    **{f'low_parlay_{n}': (n, 1.10, 1.50, True) for n in (2, 3, 4)},
    **{f'wide_parlay_{n}': (n, 1.10, 1.80, True) for n in (2, 3, 4)},
}
COMMON = ('single_control', 'single_long_3_5', 'low_parlay_2', 'low_parlay_3', 'low_parlay_4')
PAIRS = [(a, b) for a in ('single_long_3_5', 'single_long_5_10')
         for b in STRATEGIES if 'parlay' in b]


def dt(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Naive decision/kickoff timestamp')
    return parsed.astimezone(KST)


def ceil_grid(value):
    epoch = value.timestamp()
    return datetime.fromtimestamp(math.ceil(epoch / 1800) * 1800, KST)


def quote(legs):
    product = math.prod(Decimal(str(x['odds'])) for x in legs)
    if len(legs) == 1:
        return product
    return product.quantize(Decimal('.01'), rounding=ROUND_DOWN).quantize(
        Decimal('.1'), rounding=ROUND_CEILING)


def settle(legs, stake=BUDGET, components=False):
    """Do not remove unknown outcomes from the selection universe."""
    if any(x.get('hit') is None and not x.get('refund') for x in legs):
        return {'status': 'pending', 'net': None, 'hit': None, 'refunded': False}
    if components:
        portion = Decimal(stake) / len(legs)
        returned = sum((portion * (Decimal(1) if x.get('refund') else
                                  Decimal(str(x['odds'])) if x['hit'] else Decimal(0))
                        for x in legs), Decimal(0))
    elif any(not x['hit'] and not x.get('refund') for x in legs):
        returned = Decimal(0)
    else:
        active = [dict(x, odds=1 if x.get('refund') else x['odds']) for x in legs]
        returned = Decimal(stake) * quote(active)
    return {'status': 'settled', 'net': float(returned - stake),
            'hit': all(x['hit'] or x.get('refund') for x in legs),
            'refunded': all(x.get('refund') for x in legs)}


def choice_id(choice):
    return str(choice.get('choice_id') or (
        str(choice['offer_id']) + ':' + str(choice.get('selection', choice.get('label')))))


def best_ticket(states, model, strategy, ranking, checkpoint, issue):
    count, lo, hi, inclusive = STRATEGIES[strategy]
    best = []
    for frame in states.values():
        if dt(frame['available_at']) > checkpoint or dt(frame['kickoff']) < checkpoint + timedelta(minutes=30):
            continue
        if not frame.get('home') or not frame.get('away') or not frame.get('league'):
            continue
        options = []
        for c in frame['choices']:
            if (c.get('issue_year'), c.get('round')) != issue:
                continue
            if c.get('game_no') in (None, ''):
                # A known round alone cannot identify an issued product. This
                # pregame metadata gate never inspects settlement availability.
                continue
            p = c.get('scores', {}).get(model)
            if p is None or not math.isfinite(p) or not 0 < p <= 1:
                continue
            if ranking == 'estimated_ev' and model not in c.get('probability_models', frame.get('probability_models', [])):
                continue
            odds = c['odds']
            if not (lo <= odds and (hi is None or odds <= hi if inclusive else odds < hi)):
                continue
            rank = p * odds if ranking == 'estimated_ev' else p
            item = {k: c.get(k) for k in ('offer_id', 'choice_id', 'market', 'label', 'selection', 'line',
                    'issue_year', 'round', 'game_no', 'odds', 'hit', 'refund', 'result_status')}
            item.update(event_id=frame['event_id'], snapshot_id=frame['snapshot_id'],
                        home=frame['home'], away=frame['away'], kickoff=frame['kickoff'],
                        available_at=frame['available_at'], quote_observed_at=frame.get('quote_observed_at'),
                        score=p, rank=rank, selection_key=choice_id(c),
                        probability=model in c.get('probability_models', frame.get('probability_models', [])))
            options.append(item)
        if options:
            best.append(min(options, key=lambda x: (-x['rank'], x['selection_key'])))
    best.sort(key=lambda x: (-x['rank'], x['event_id'], x['selection_key']))
    best = best[:20]
    chosen, value = None, -1
    for combination in itertools.combinations(best, count):
        score = math.prod(x['rank'] for x in combination)
        if score < value:
            continue
        teams = [str(x[t]).strip().casefold() for x in combination for t in ('home', 'away')]
        if len(set(teams)) != len(teams):
            continue
        if score > value:
            chosen, value = list(combination), score
        # First N sorted candidates are an absolute upper bound.
        if combination == tuple(best[:count]):
            break
    return chosen


def ticket(legs, strategy, checkpoint):
    result = {'strategy': strategy, 'placement': checkpoint.isoformat(), 'stake': BUDGET,
              'quoted_odds': float(quote(legs)), 'legs': legs, **settle(legs)}
    if all(x['probability'] for x in legs):
        result['independence_probability_diagnostic'] = math.prod(x['score'] for x in legs)
        result['independence_ev_diagnostic'] = result['quoted_odds'] * math.prod(x['score'] for x in legs) - 1
    if len(legs) > 1:
        result['equal_budget_component_singles'] = settle(legs, components=True)
    return result


def read_frames(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def build_sessions(frames):
    groups = defaultdict(list)
    for f in frames:
        day = dt(f['kickoff']).date().isoformat()
        groups[(f['cohort'], f.get('league') or 'UNKNOWN', day)].append(f)
    sessions, audit = [], Counter()
    for (cohort, league, day), revisions in sorted(groups.items()):
        revisions.sort(key=lambda f: (dt(f['available_at']), f.get('ledger_sequence', 0), f['snapshot_id']))
        # Latest event revisions are always selected BEFORE checking model presence.
        checkpoints = sorted({ceil_grid(dt(f['available_at'])) for f in revisions})
        models = sorted({m for f in revisions for c in f['choices'] for m in c.get('scores', {})})
        issues = sorted({(c.get('issue_year'), c.get('round')) for f in revisions for c in f['choices']
                         if c.get('issue_year') is not None and c.get('round') is not None}, key=str)
        audit['day_league_groups'] += 1
        states, idx, seen = {}, 0, set()
        for checkpoint in checkpoints:
            while idx < len(revisions) and dt(revisions[idx]['available_at']) <= checkpoint:
                f = revisions[idx]
                states[(f['event_id'], f['kickoff'])] = f
                idx += 1
            for issue in issues:
                cache = {}
                for model in models:
                    for ranking in ('model_score', 'estimated_ev'):
                        if ranking == 'estimated_ev' and not any(model in f.get('probability_models', []) for f in states.values()):
                            continue
                        base = (cohort, league, day, issue, model, ranking)
                        tickets = {}
                        for strategy in STRATEGIES:
                            legs = best_ticket(states, model, strategy, ranking, checkpoint, issue)
                            if legs:
                                tickets[strategy] = legs
                        cache[(model, ranking)] = tickets
                        contexts = [('opportunity:' + name, [name]) for name in tickets]
                        contexts += [('pair:' + a + ':' + b, [a, b]) for a, b in PAIRS
                                     if a in tickets and b in tickets]
                        if all(name in tickets for name in COMMON):
                            contexts.append(('common_all', list(COMMON)))
                        for context, names in contexts:
                            key = (*base, context, tuple(names))
                            if key in seen:
                                continue
                            seen.add(key)
                            sessions.append({'cohort': cohort, 'league': league, 'day': day,
                                             'issue': list(issue), 'model': model, 'ranking': ranking,
                                             'context': context,
                                             'availability_proven': all(f.get('availability_proven', False) for f in states.values()),
                                             'tickets': {name: ticket(tickets[name], name, checkpoint) for name in names}})
                # Models must be compared on a single common placement checkpoint,
                # not their individually earliest, differently available cases.
                for ranking in ('model_score', 'estimated_ev'):
                    required = [m for m in models if ranking == 'model_score' or
                                any(m in f.get('probability_models', []) for f in revisions)]
                    if len(required) < 2 or not all((m, ranking) in cache for m in required):
                        continue
                    contexts = [('pair:' + a + ':' + b, [a, b]) for a, b in PAIRS]
                    contexts += [('common_all', list(COMMON))]
                    for context, names in contexts:
                        common_key = (cohort, league, day, issue, ranking, 'model_common:' + context)
                        if common_key in seen or not all(all(n in cache[(m, ranking)] for n in names) for m in required):
                            continue
                        seen.add(common_key)
                        built = {m: {n: ticket(cache[(m, ranking)][n], n, checkpoint) for n in names} for m in required}
                        all_settled = all(t['net'] is not None for ts in built.values() for t in ts.values())
                        for model in required:
                            sessions.append(dict(cohort=cohort, league=league, day=day, issue=list(issue),
                                                 model=model, ranking=ranking, context='model_common:' + context,
                                                 models_compared=required, cross_model_complete=all_settled,
                                                 tickets=built[model]))
        audit['event_revisions'] += len(revisions)
    audit['sessions'] = len(sessions)
    return sessions, dict(audit)


def metrics(rows):
    done = sorted((r for r in rows if r['net'] is not None), key=lambda r: (r['day'], r.get('placement', '')))
    n = len(done)
    if not n:
        return {'n': 0, 'pending': len(rows), 'roi_pct': None}
    profits = [r['net'] for r in done]
    daily, months = defaultdict(float), defaultdict(float)
    for r in done:
        daily[r['day']] += r['net']
        months[r['day'][:7]] += r['net']
    equity = peak = drawdown = streak = maxstreak = 0
    for day in sorted(daily):
        equity += daily[day]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    for profit in profits:
        streak = streak + 1 if profit < 0 else 0
        maxstreak = max(maxstreak, streak)
    stake = sum(r['stake'] for r in done)
    return {'n': n, 'pending': len(rows) - n, 'dates': len(daily),
            'stake_krw': stake, 'net_krw': round(sum(profits), 2),
            'roi_pct': round(100 * sum(profits) / stake, 4),
            'whole_ticket_hit_pct': round(100 * sum(bool(r['hit']) for r in done) / n, 4),
            'all_void': sum(r['refunded'] for r in done),
            'mean_quoted_odds': round(sum(r.get('quoted_odds', 0) for r in done) / n, 4),
            'daily_close_max_drawdown_krw': round(drawdown, 2),
            'max_losing_ticket_streak': maxstreak,
            'profitable_months': sum(v > 0 for v in months.values()), 'months': len(months),
            'monthly_net': {k: round(v, 2) for k, v in sorted(months.items())}}


def band(odds):
    for lo, hi in ((1, 2), (2, 3), (3, 5), (5, 10), (10, 1000)):
        if lo <= odds < hi:
            return f'{lo}-{hi}'
    return 'other'


def bootstrap_delta(pairs):
    byday = defaultdict(list)
    for day, delta in pairs:
        byday[day].append(delta)
    if len(byday) < 10:
        return {'dates': len(byday), 'ci95_delta_roi_pp': None, 'reason': 'fewer_than_10_dates'}
    blocks = [(sum(v), len(v)) for v in byday.values()]
    rng = random.Random(20261030)
    vals = []
    for _ in range(1000):
        sampled = rng.choices(blocks, k=len(blocks))
        vals.append(100 * sum(x[0] for x in sampled) / (BUDGET * sum(x[1] for x in sampled)))
    vals.sort()
    return {'dates': len(byday), 'ci95_delta_roi_pp': [round(vals[24], 3), round(vals[974], 3)],
            'method': 'kickoff-day cluster bootstrap, previously exposed archive, descriptive not multiplicity-adjusted'}


def summarize(sessions):
    grouped = defaultdict(list)
    for s in sessions:
        grouped[(s['cohort'], s['model'], s['league'], s['ranking'], s['context'],
                 tuple(s.get('models_compared', [])))].append(s)
    output, tuning = [], []
    for (cohort, model, league, ranking, context, model_set), group in sorted(grouped.items()):
        names = sorted(group[0]['tickets'])
        complete = [s for s in group if s.get('cross_model_complete', True) and
                    all(t['net'] is not None for t in s['tickets'].values())]
        record = dict(cohort=cohort, model=model, league=league, ranking=ranking,
                      context=context, models_compared=list(model_set), selected_sessions=len(group), jointly_settled_sessions=len(complete),
                      pending_sessions=len(group)-len(complete), strategies={})
        # Pair/common comparisons have identical settled tickets and total stakes.
        for name in names:
            rows = [dict(s['tickets'][name], day=s['day']) for s in complete]
            entry = metrics(rows)
            entry['payout_bands'] = {b: metrics([r for r in rows if band(r['quoted_odds']) == b])
                                     for b in sorted({band(r['quoted_odds']) for r in rows})}
            if 'parlay' in name:
                controls = [dict(r, **r['equal_budget_component_singles']) for r in rows]
                entry['same_legs_as_separate_singles'] = metrics(controls)
            record['strategies'][name] = entry
        if 'pair:' in context:
            a, b = context.rsplit(':', 2)[1:]
            record['delta_b_minus_a'] = bootstrap_delta([
                (s['day'], s['tickets'][b]['net']-s['tickets'][a]['net']) for s in complete])
            record['b_better_dates'] = sum(sum(s['tickets'][b]['net']-s['tickets'][a]['net'] for s in complete if s['day'] == d) > 0
                                           for d in {s['day'] for s in complete})
        output.append(record)
        if context.endswith('common_all'):
            dates = sorted({s['day'] for s in complete})
            cut = len(dates)*2//3
            train = [s for s in complete if s['day'] in dates[:cut]]
            test = [s for s in complete if s['day'] in dates[cut:]]
            item = dict(cohort=cohort, model=model, league=league, ranking=ranking, context=context, models_compared=list(model_set),
                        train_n=len(train), test_n=len(test), train_dates=dates[:cut], test_dates=dates[cut:])
            if len(train) < 10 or len(test) < 5:
                item['status'] = 'insufficient'
            else:
                train_scores = {name: metrics([dict(s['tickets'][name], day=s['day']) for s in train]) for name in COMMON}
                winner = min(COMMON, key=lambda name: (-train_scores[name]['roi_pct'], name))
                item.update(status='retrospective_train_selected', selected=winner, train=train_scores,
                            evaluation={name: metrics([dict(s['tickets'][name], day=s['day']) for s in test]) for name in COMMON})
            tuning.append(item)
    return output, tuning


def dump(path, value, lines=False):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'wt', encoding='utf-8', newline='\n') as f:
        if lines:
            for row in value:
                f.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n')
        else:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    audit, sessions, sources = [], [], []
    for source in args.inputs:
        path = Path(source)
        frames = read_frames(path)
        result, counts = build_sessions(frames)
        sessions.extend(result)
        audit.append(dict(source=path.name, parent=path.parent.name, **counts))
        sources.append(dict(path=path.as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), frames=len(frames)))
        print(json.dumps(audit[-1]), flush=True)
    summary, tuning = summarize(sessions)
    dump(out/'sessions.jsonl.gz', sessions, lines=True)
    dump(out/'summary.json.gz', summary)
    dump(out/'league-selection.json', tuning)
    dump(out/'manifest.json', {'inputs': sources, 'audit': audit, 'summary_groups': len(summary),
                             'budget': BUDGET, 'production_changed': False, 'monthly_loss_cap_applied': False,
                             'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({'sessions': len(sessions), 'summary_groups': len(summary), 'tuning_groups': len(tuning)}))


if __name__ == '__main__':
    main()
