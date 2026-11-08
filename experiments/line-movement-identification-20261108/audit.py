"""Read-only inventory and sharp probability bounds for nested half-line events."""
import argparse
import collections
import datetime
import hashlib
import json
from pathlib import Path


def bounds(old_line, new_line, p_new, side):
    """Sharp bounds assuming integer scores, identical period/unit, no push.

    `under`: X < line; `over`: X > line. This covers totals or a
    consistently oriented threshold on a score margin, not 3-way markets.
    p_new is an assumed probability, not a claim that a quote is calibrated.
    """
    if side not in ('under', 'over') or not 0 <= p_new <= 1:
        raise ValueError('invalid side or probability')
    if old_line % 1 != .5 or new_line % 1 != .5:
        raise ValueError('half-lines required')
    if old_line == new_line:
        return [p_new, p_new]
    old_subset = (old_line < new_line) == (side == 'under')
    return [0.0, p_new] if old_subset else [p_new, 1.0]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


def direction_candidates(history, response):
    # One fixed rule: after captured_at, choose first pre-kickoff observation
    # whose lower-price side differs from the frozen market-baseline selection.
    # These are candidate counts, not verified settled comparisons.
    sides = {'언더오버': ['언더', '오버'], '핸디캡': ['핸디홈', '핸디원정']}
    counts = collections.Counter()
    for game in response['live']:
        record = game.get('prediction_record') or {}
        if not record.get('kickoff') or not record.get('captured_at'):
            continue
        kickoff, captured = timestamp(record['kickoff']), timestamp(record['captured_at'])
        for item in (record.get('market_recommendations') or {}).get('items', []):
            if item.get('model') != 'market-baseline-v1':
                continue
            rows = history['history'].get(str(game.get('round')), {}).get(str(item.get('game_no')), [])
            if not rows:
                continue
            counts['market_baseline_frozen_items_with_history'] += 1
            options = sides.get(item.get('market'))
            if not options or item.get('selection') not in options:
                continue
            old_side = options.index(item['selection'])
            candidates = []
            for row in rows:
                odds = row.get('odds') or []
                observed = timestamp(row['observed_at'])
                if (captured < observed < kickoff and len(odds) == 2 and odds[0] != odds[1]
                        and (0 if odds[0] < odds[1] else 1) != old_side):
                    candidates.append((observed, row))
            if not candidates:
                continue
            observed, row = min(candidates, key=lambda pair: pair[0])
            counts['first_prestart_direction_flip'] += 1
            if observed <= kickoff - datetime.timedelta(minutes=30):
                counts['first_flip_by_t30'] += 1
            final = history['markets'].get(str(game.get('round')), {}).get(str(item.get('game_no')))
            if final and final.get('label') == row.get('label') and final.get('result') in ['언더', '오버', '핸디승', '핸디패']:
                counts['first_flip_with_matching_final_label_and_result'] += 1
    return {key: counts[key] for key in ('market_baseline_frozen_items_with_history', 'first_prestart_direction_flip', 'first_flip_by_t30', 'first_flip_with_matching_final_label_and_result')}


def broad_inventory(history, response):
    matches = collections.defaultdict(dict)
    for section in ('live', 'past'):
        for game in response[section]:
            identity = tuple(game.get(k) for k in ('round', 'date', 'home', 'away', 'sport'))
            for option in game.get('options', []):
                no = option.get('게임번호')
                if no is not None:
                    matches[(str(game.get('round')), str(no))][identity] = game
    totals, sports, flip_sports, models = (collections.Counter() for _ in range(4))
    for rnd, games in history['history'].items():
        for no, rows in games.items():
            if len({r.get('line') for r in rows if r.get('line') is not None}) < 2:
                continue
            candidates = matches.get((str(rnd), str(no)), {})
            if len(candidates) != 1:
                totals['unmatched_or_ambiguous'] += 1
                continue
            game = next(iter(candidates.values()))
            totals['unique_candidate_product_keys'] += 1
            sports[str(game.get('sport'))] += 1
            favorites = []
            for row in sorted(rows, key=lambda r: timestamp(r['observed_at'])):
                odds = row.get('odds') or []
                if len(odds) == 2 and odds[0] != odds[1]:
                    favorites.append(0 if odds[0] < odds[1] else 1)
            if len(set(favorites)) > 1:
                totals['any_two_way_favorite_flip'] += 1
                flip_sports[str(game.get('sport'))] += 1
            record = game.get('prediction_record') or {}
            for item in (record.get('market_recommendations') or {}).get('items', []):
                if str(item.get('game_no')) == str(no):
                    models[str(item.get('model', 'unknown'))] += 1
    return {'counts': dict(totals), 'by_sport': dict(sports), 'flips_by_sport': dict(flip_sports), 'recorded_recommendation_models': dict(models)}


def run(history_path, response_path):
    history = json.loads(history_path.read_text(encoding='utf-8-sig'))
    response = json.loads(response_path.read_text(encoding='utf-8-sig'))
    moves = {}
    products = observations = same_price = 0
    categories = collections.Counter()
    for rnd, games in history['history'].items():
        for no, rows in games.items():
            products += 1
            observations += len(rows)
            if len({r.get('line') for r in rows if r.get('line') is not None}) < 2:
                continue
            moves[(str(rnd), str(no))] = rows
            categories[rows[0].get('market', '')] += 1
            same_price += any(a.get('line') != b.get('line') and a.get('line') is not None and b.get('line') is not None and a.get('odds') == b.get('odds') for i, a in enumerate(rows) for b in rows[i+1:])
    # Association only: current response items do not prove a frozen revision.
    candidates = collections.Counter()
    associated = set()
    for game in response['live']:
        rec = game.get('market_recommendations') or {}
        for item in rec.get('items', []):
            key = (str(game.get('round')), str(item.get('game_no')))
            if key in moves:
                associated.add(key)
                candidates[str(game.get('sport'))] += 1
    return {
        'input_sha256': {'history': digest(history_path), 'response': digest(response_path)},
        'history_generated_at': history.get('generated_at'),
        'response_generated_at': response.get('generated_at'),
        'history_rounds': len(history['history']), 'products': products,
        'observations': observations, 'distinct_line_product_keys': len(moves),
        'distinct_line_by_first_market': dict(categories),
        'distinct_line_with_any_equal_odds_pair': same_price,
        'current_response_candidate_keys': len(associated),
        'current_response_candidate_items_by_sport': dict(candidates),
        'broad_identifier_inventory': broad_inventory(history, response),
        'direction_change_candidate_inventory': direction_candidates(history, response),
        'eligible_paired_models': None,
        'eligibility_note': 'Candidates are identifiers only; frozen revision, kickoff, identity, pricing, time, distribution and official settlement have not been established.',
        'illustrative_bounds_assuming_new_probability': {
            'old_under_8.5_new_under_9.5_p_new_0.5': bounds(8.5, 9.5, .5, 'under'),
            'old_over_8.5_new_over_9.5_p_new_0.5': bounds(8.5, 9.5, .5, 'over'),
        },
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(args.history, args.response), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
