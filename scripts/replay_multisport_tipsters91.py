"""Recheck archived article joins and calculate fixed 9:1 single-pick returns.

Retrospective sensitivity analysis, not a pregame-certified backtest. Preserve
the distinction between reconstructed research baseline and market baseline.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devig import market_probabilities


def choices(row):
    p = row['probability']
    n = 3 if row['sport'] == 'sc' else 2
    odds, tip, result = row['odds'], row['choices']['tipster_only'], row['y']
    if (len(p) != n or len(odds) != n or type(tip) is not int or tip not in range(n)
            or type(result) is not int or result not in range(n)
            or any(not math.isfinite(x) or not 0 <= x <= 1 for x in p)
            or not math.isclose(sum(p), 1, abs_tol=1e-6)
            or any(not math.isfinite(x) or x <= 1 for x in odds)):
        raise ValueError('invalid evaluation row')
    market = market_probabilities(odds)
    vote = [float(i == tip) for i in range(n)]
    vectors = {'research_baseline': p, 'market_baseline': market, 'tipster': vote,
               'research90_tipster10': [.9*a + .1*b for a, b in zip(p, vote)],
               'market90_tipster10': [.9*a + .1*b for a, b in zip(market, vote)]}
    return {name: max(range(n), key=lambda i: q[i]) for name, q in vectors.items()}


def score(rows, policy):
    pnl = [r['odds'][r['decisions'][policy]] * (r['decisions'][policy] == r['y']) - 1 for r in rows]
    return dict(n=len(rows), hits=sum(r['decisions'][policy] == r['y'] for r in rows),
                roi_pct=100*sum(pnl)/len(rows) if rows else None,
                net_units=sum(pnl), net_krw_at_10000=round(10000*sum(pnl)))


def capped_proxy(rows, policy):
    # Conservative daily batches: reserve a full stake for all same-day tickets
    # before applying any win. Next-day settlement is an assumption, not observed.
    months, accepted = defaultdict(float), []
    days = defaultdict(list)
    for row in rows:
        days[row['date']].append(row)
    for day, batch in sorted(days.items()):
        month, selected = day[:7], []
        for r in sorted(batch, key=lambda r: (r['sport'], r['home'], r['away'])):
            if months[month] - (len(selected)+1)*10000 >= -50000 - 1e-8:
                selected.append(r)
        months[month] += score(selected, policy)['net_krw_at_10000']
        accepted.extend(selected)
    return dict(**score(accepted, policy), monthly_net=dict(months),
                assumption='same-day full exposure reserved; settled next day; no weekly frequency cap')


def run(archive, games):
    from accuracy_formula_lab import load_unique_market_rows
    old = json.loads(archive.read_text(encoding='utf8'))
    expected = next(v for k, v in old['hashes'].items() if k.endswith('games.csv'))
    digest = hashlib.sha256(games.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError('historical source hash changed')
    raw, quality = load_unique_market_rows(games)
    index = defaultdict(list)
    for r in raw:
        k = r['key']
        if (r['sport'] == 'sc' and k[5] == '승무패' and r['n_way'] == 3 or
                r['sport'] in ('bk', 'bs', 'vl') and k[5] == '승패' and r['n_way'] == 2):
            index[(r['sport'], str(r['date'].date()), k[3], k[4])].append(r)
    ledger, seen = [], set()
    for r in old['ledger']:
        key = (r['sport'], r['date'], r['home'], r['away'])
        if key in seen:
            raise ValueError('duplicate physical fixture')
        seen.add(key)
        matches = index[key]
        if len(matches) != 1 or list(matches[0]['odds']) != r['odds'] or matches[0]['winner'] != r['y']:
            raise ValueError(f'join/result/price mismatch: {key}')
        ledger.append({**r, 'decisions': choices(r), 'raw_join_verified': True})
    names = list(choices(ledger[0]))
    groups = {s: [r for r in ledger if r['sport'] == s] for s in ('bs', 'bk', 'sc', 'vl')}
    return dict(status='retrospective_unverified_timing_not_adoption_evidence', strict_eligible=0,
                weight_tipster=.1, new_article_count=0, unit_stake=10000, source_quality=quality,
                baseline='archived 2023-2024 trained research probabilities, NOT frozen production',
                timing='article and odds pregame availability unproven; article edits not excluded',
                results='local settlement CSV rejoined; official independent recheck not performed',
                vote='single-author one-hot vote, NOT claimed 100% confidence',
                hashes={'archive': hashlib.sha256(archive.read_bytes()).hexdigest(), 'games': digest},
                sports={s: {name: score(rows, name) for name in names} for s, rows in groups.items()},
                changed_picks={s: sum(r['decisions']['research_baseline'] != r['decisions']['research90_tipster10'] for r in rows)
                               for s, rows in groups.items()},
                monthly_loss_cap_proxy={name: capped_proxy(ledger, name) for name in names}, ledger=ledger)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--games', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    result = run(args.archive, args.games)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k:result[k] for k in ('sports','changed_picks','monthly_loss_cap_proxy')}, ensure_ascii=False))
