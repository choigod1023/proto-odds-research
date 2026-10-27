"""Exploratory local audit: first-observed odds, never latest/post-result odds.

Publisher settlements are NOT official result verification or incremental model ROI.
No production imports, DB access, source ranking, or automatic model promotion.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


def timestamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.utcoffset() is None:
        raise ValueError('timezone required')
    return value


def decimal_odds(value):
    if isinstance(value, bool):
        raise ValueError('invalid odds')
    value = float(value)
    if not math.isfinite(value) or abs(value) < 100:
        raise ValueError('invalid American odds')
    return 1 + (value / 100 if value > 0 else 100 / -value)


def audit(events):
    groups = defaultdict(list)
    excluded = Counter()
    for row in events:
        try:
            timestamp(row['observed_at'])
            if not row.get('pick_id'):
                raise ValueError('missing id')
        except (KeyError, TypeError, ValueError, AttributeError):
            excluded['invalid_event'] += 1
            continue
        groups[row['pick_id']].append(row)
    accepted = []
    for pick_id, rows in sorted(groups.items()):
        rows.sort(key=lambda r: timestamp(r['observed_at']))
        first = rows[0]
        if not (first.get('event_type') == 'first_observed'
                and first.get('identity_version') in (2, 3, 4)
                and first.get('eligible_pre_event') is True
                and first.get('game_started') is False
                and not first.get('is_baseline')
                and not first.get('is_parlay_leg')
                and first.get('result') is None):
            excluded['not_first_unsettled_pregame_single'] += 1
            continue
        try:
            odds = decimal_odds(first.get('american_odds'))
        except (TypeError, ValueError):
            excluded['missing_or_invalid_first_odds'] += 1
            continue
        identity = ('selection', 'slate_date', 'team_a', 'team_b', 'market_type', 'slug')
        if any(not first.get(k) for k in identity):
            excluded['missing_identity'] += 1
            continue
        if any(any(r.get(k) != first[k] for k in identity) for r in rows):
            excluded['identity_changed'] += 1
            continue
        finals = [r for r in rows if r.get('result') in ('W', 'L', 'P', 'V')]
        if not finals:
            excluded['unsettled'] += 1
            continue
        if len({r['result'] for r in finals}) != 1:
            excluded['conflicting_settlement'] += 1
            continue
        settled = finals[0]
        if timestamp(settled['observed_at']) <= timestamp(first['observed_at']):
            excluded['settled_at_first_observation'] += 1
            continue
        result = settled['result']
        accepted.append(dict(pick_id=pick_id, market=first['market_type'],
            date=first['slate_date'], observed_at=first['observed_at'],
            settled_observed_at=settled['observed_at'], odds=odds, result=result,
            profit=odds-1 if result == 'W' else -1 if result == 'L' else 0,
            fingerprint=tuple(first[k] for k in identity)))
    # Ambiguous duplicate identities are all excluded, never choose by outcome.
    counts = Counter(r['fingerprint'] for r in accepted)
    clean = [r for r in accepted if counts[r['fingerprint']] == 1]
    excluded['duplicate_identity'] += len(accepted)-len(clean)
    def metrics(rows):
        decisive = [r for r in rows if r['result'] in ('W', 'L')]
        wins = sum(r['result'] == 'W' for r in decisive)
        return dict(n=len(rows), decisive=len(decisive), wins=wins,
                    hit_rate_pct=100*wins/len(decisive) if decisive else None,
                    roi_pct=100*sum(r['profit'] for r in rows)/len(rows) if rows else None)
    markets = {m: metrics([r for r in clean if r['market'] == m])
               for m in sorted({r['market'] for r in clean})}
    days = {d: metrics([r for r in clean if r['date'] == d])
            for d in sorted({r['date'] for r in clean})}
    return dict(schema='frozen-tipster-audit-v1', unique_pick_ids=len(groups),
        excluded=dict(excluded), overall=metrics(clean), by_market=markets, by_day=days,
        official_verified_n=0, paired_three_arm_n=0, incremental_roi_pct=None,
        monthly_loss_limit_krw=50000, monthly_two_leg_simulation='not_run',
        limitations=['Publisher results only; no official event/result link.',
            'Pregame status is collector assertion, not verified official kickoff.',
            'No sport field; do not infer sport from team abbreviations.',
            'No frozen facts/challenger predictions; not a comparison to our model.',
            'Multiple correlated picks; short selected-source window; no independence claim.',
            'Flat single stakes including voids in turnover; not user monthly returns.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = audit(json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip())
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
