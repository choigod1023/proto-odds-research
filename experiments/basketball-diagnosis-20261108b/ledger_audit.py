"""Read only the provided snapshot-selected ledger; emit anonymous aggregates."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path

NOW = datetime(2026, 11, 8, 6, 41, 29, tzinfo=timezone.utc)
LEAGUES = {'NBA', 'KBL', 'WKBL'}


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def summarize(rows):
    predictions, settlements = {}, defaultdict(list)
    for row in rows:
        if row.get('record_type') == 'prediction':
            if row['snapshot_id'] in predictions:
                raise ValueError('duplicate prediction snapshot')
            predictions[row['snapshot_id']] = row
        elif row.get('record_type') == 'settlement':
            settlements[row['snapshot_id']].append(row)
    out = defaultdict(Counter)
    seen_events = Counter()
    featured_mix = Counter()
    for sid, row in predictions.items():
        fixture = row['features']['fixture']
        league = fixture.get('league')
        if fixture.get('sport') != 'bk' or league not in LEAGUES:
            continue
        seen_events[row['event_id']] += 1
        start, captured = stamp(row['kickoff']), stamp(row['captured_at'])
        timing = 'before_t30' if captured < start - timedelta(minutes=30) else 'at_or_after_t30'
        ss = settlements.get(sid, [])
        last = max(ss, key=lambda x: x['ledger_sequence']) if ss else {}
        outcome = last.get('outcome') or {}
        pred = row['predictions']
        fp = pred.get('featured_pick') or {}
        if fp:
            primary_odds = pred.get('odds')
            bucket = ('missing' if not isinstance(primary_odds, (int, float)) else
                      'under_1.50' if primary_odds < 1.5 else '1.50_plus')
            featured_mix[f'{league}/{fp.get("market", "missing")}/primary_{bucket}'] += 1
        hybrid = row['features'].get('tipster_hybrid') or {}
        policy = row['model'].get('selection_policy') or 'missing'
        authors = hybrid.get('authors')
        author_group = str(authors) if authors is not None else 'missing'
        source = hybrid.get('selection_source') or ('votes' if authors else 'other')

        def add(window, kind, market, model, saved, settled):
            result, odds = settled.get('result'), saved.get('odds')
            if result and settled.get('selection_id') != saved.get('selection_id'):
                result = 'settlement_identity_mismatch'
            if result and kind != 'primary_saved_selection':
                for key in ('offer_id', 'market', 'settlement_rule', 'line', 'odds'):
                    a, b = saved.get(key), settled.get(key)
                    if key in ('line', 'odds') and a is not None and b is not None:
                        try:
                            a, b = Decimal(str(a)), Decimal(str(b))
                        except InvalidOperation:
                            result = 'settlement_terms_mismatch'
                    if a != b:
                        result = 'settlement_terms_mismatch'
            result = result or 'no_settlement_result'
            price = 'missing' if not isinstance(odds, (int, float)) else ('under_1.50' if odds < 1.5 else '1.50_to_1.99' if odds < 2 else '2_plus')
            prefix = f'{window}/{league}/{kind}/{market}/{model}/{timing}'
            out[prefix][result] += 1
            out[prefix + '/odds/' + price][result] += 1

        for days in (7, 14, 30):
            if not NOW - timedelta(days=days) <= start <= NOW:
                continue
            window = f'{days}d' if days == 7 else f'{days}d_partial_from_2026-10-28'
            base = f'{policy}:{source}:authors_{author_group}'
            add(window, 'primary_saved_selection', pred.get('market') or 'missing', base,
                pred, outcome)
            for item in (pred.get('market_recommendations') or {}).get('items') or []:
                result = (outcome.get('market_recommendation_results') or {}).get(item.get('offer_id')) or {}
                add(window, 'market_recommendation', item.get('market') or 'missing',
                    item.get('model') or 'missing', item, result)
            featured = pred.get('featured_pick') or {}
            if featured:
                result = outcome.get('featured_pick_result') or {}
                add(window, 'featured', featured.get('market') or 'missing', 'featured-pregame-v1',
                    featured, result)
    return {'groups': {k: dict(sorted(v.items())) for k, v in sorted(out.items())},
            'featured_mix_all_kickoffs': dict(sorted(featured_mix.items())),
            'basketball_snapshot_count': sum(seen_events.values()),
            'basketball_event_ids_with_multiple_snapshots': sum(v > 1 for v in seen_events.values()),
            'basketball_extra_snapshots': sum(v - 1 for v in seen_events.values() if v > 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    rows = [json.loads(line) for line in gzip.decompress(raw).splitlines()]
    result = summarize(rows)
    result['source_sha256'] = hashlib.sha256(raw).hexdigest()
    result['scope'] = 'snapshot IDs selected from API; not complete ledger or complete hash chain'
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
