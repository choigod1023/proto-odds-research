"""Replay frozen score forecasts against settled Betman full-game products.

Archive receipts are retrospective. This command does not publish predictions.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from scipy.stats import poisson, skellam

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from basketball_archive_replay import replay as replay_basketball
from market_outcomes import ALIASES, settle
from market_pick_frontier import score_pick
from proto_full_game_markets import full_game_product

STATE = ROOT / 'experiments/dynamic-score-state-model-20261030'
MANIFEST = ROOT / 'experiments/pregame-proto-prices-20261104/manifest.json'
LEAGUES = {'K리그1': 'sc', 'K리그2': 'sc', 'NPB': 'bs', 'MLB': 'bs', 'KBL': 'bk', 'NBA': 'bk'}
MODELS = ('score-static', 'score-dynamic')
LABELS = {'under': '언더', 'over': '오버', 'home': '핸디홈', 'draw': '핸디무', 'away': '핸디원정'}
KST = timezone(timedelta(hours=9))
BASEBALL_NAMES = {
    'MLB': {
        '디트로이트타이거즈': '디트로이트', '마이애미말린스': '마이애미',
        '미네소타트윈스': '미네소타', '밀워키브루어스': '밀워키', '보스턴레드삭스': '보스턴',
        '볼티모어오리올스': '볼티모어', '샌디에이고파드리스': '샌디에이고',
        '샌프란시스코자이언츠': '샌프란시스코', '세인트루이스카디널스': '세인트루이스',
        '시애틀매리너스': '시애틀', '시카고화이트삭스': '시카고W', '신시내티레즈': '신시내티',
        '애리조나다이아몬드백스': '애리조나', '애틀랜타브레이브스': '애틀랜타',
        '워싱턴내셔널스': '워싱턴', '캔자스시티로얄스': '캔자스시티', '콜로라도로키스': '콜로라도',
        '클리블랜드가디언스': '클리블랜드', '탬파베이레이스': '탬파베이', '텍사스레인저스': '텍사스',
        '토론토블루제이스': '토론토', '피츠버그파이어리츠': '피츠버그',
        '필라델피아필리스': '필라델피아', '휴스턴애스트로스': '휴스턴',
    },
    'NPB': {
        '닛폰햄파이터스': '닛폰햄', '홋카이도닛폰햄파이터스': '닛폰햄',
        '도호쿠라쿠텐골든이글스': '라쿠텐', '라쿠텐골든이글스': '라쿠텐',
        '사이타마세이부라이온즈': '세이부', '세이부라이온즈': '세이부',
        '도쿄야쿠르트스왈로스': '야쿠르트', '야쿠르트스왈로스': '야쿠르트',
        '오릭스버팔로스': '오릭스', '요미우리자이언츠': '요미우리',
        '주니치드래건스': '주니치', '지바롯데마린스': '지바롯데', '한신타이거즈': '한신',
    },
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unpack(raw):
    return gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw


def jsonl(raw):
    return [json.loads(line) for line in unpack(raw).splitlines() if line.strip()]


def clock(value):
    at = datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError('timezone required')
    return at


def result_row(row):
    return (str(row.get('protoStatus')) in {'4', '20'} and row.get('gameReject') == '0'
            and row.get('buyReject') == '0' and row.get('gameSubject') in (None, ''))


def ordinary_score(row, sport):
    code = tuple(row.get(field) for field in ('betId', 'betTypId', 'betNm'))
    if row['handi'] != 0 or not result_row(row):
        return None
    accepted = {('1', '1', '축구 승무패')} if sport == 'sc' else (
        {('2', '2', '야구 승패'), ('108', '3', '야구 승1패')} if sport == 'bs' else {('109', '3', '농구 승5패')})
    if code != (None, None, None) and code not in accepted:
        return None
    match = re.fullmatch(r'(\d+):(\d+)', str(row.get('mchScore')))
    return tuple(map(int, match.groups())) if match else None


def check_result(raw, market, score, sport, league):
    h, a = score
    line = Decimal(market.line)
    expected_score = (Decimal(h + a), Decimal(0)) if market.kind == 'total' else (h + line, Decimal(a))
    try:
        if tuple(Decimal(x) for x in raw['mchScore'].split(':')) != expected_score:
            return None
    except (KeyError, ValueError, ArithmeticError, AttributeError):
        return None
    terms = dict(game_no=market.game_no, market=market.kind, line=market.line, period=market.period,
                 settlement_rule=market.rule, n_way=len(market.selections), sport=sport, league=league,
                 unit={'sc': 'goals', 'bs': 'runs', 'bk': 'points'}[sport])
    outcome = next((side for side in market.selections
                    if settle(terms, side, h, a, 'final', result_period=market.period)['hit']), None)
    expected_code = {'home': '0', 'draw': '1', 'away': '2', 'under': '0', 'over': '2'}.get(outcome)
    return outcome if expected_code == str(raw.get('gameResult')) else None


def read_rounds(manifest, roots):
    for entry in manifest['sources']:
        root = roots[entry['source_root_index']].resolve()
        path = (root / entry['body_file']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('archive path outside source root')
        body = unpack(path.read_bytes())
        if sha(body) != entry['body_sha256']:
            raise ValueError('archive hash mismatch')
        packet = json.loads(body)
        if str(packet['gmTs']).zfill(6) != entry['round'] or packet['currentLottery']['gmId'] != 'G101':
            raise ValueError('archive round mismatch')
        schedule = packet['compSchedules']
        if len(set(schedule['keys'])) != len(schedule['keys']):
            raise ValueError('ambiguous archive schema')
        rows = [dict(zip(schedule['keys'], row, strict=True)) for row in schedule['datas']]
        if len({str(row['matchSeq']) for row in rows}) != len(rows):
            raise ValueError('duplicate product in round')
        yield entry['round'], entry['body_sha256'], rows


def fixture_key(row):
    return (row['leagueName'], datetime.fromtimestamp(row['gameDate']/1000, KST),
            row['homeName'], row['awayName'])


def archived_products(rounds):
    products = []
    for round_no, body_sha, rows in rounds:
        fixtures = defaultdict(list)
        for row in rows:
            sport = LEAGUES.get(row.get('leagueName'))
            if (sport and str(row.get('itemCode')).lower() == sport
                    and row.get('homeId') and row.get('awayId') and row['homeId'] != row['awayId']
                    and result_row(row)):
                fixtures[(*fixture_key(row), row['homeId'], row['awayId'])].append(row)
        for key, members in fixtures.items():
            league = key[0]
            sport = LEAGUES[league]
            scores = {score for row in members if (score := ordinary_score(row, sport)) is not None}
            if len(scores) != 1:
                continue
            score = next(iter(scores))
            for row in members:
                try:
                    parsed = full_game_product(row, sport)
                except (TypeError, ValueError, KeyError):
                    continue
                if parsed:
                    market, prices = parsed
                    outcome = check_result(row, market, score, sport, league)
                    if outcome:
                        products.append((round_no, body_sha, key, market, prices, outcome, score))
    return products


def model_masses(market, audit, quadrature):
    nodes, weights = quadrature(np.asarray(audit['linear_mean']), np.asarray(audit['linear_covariance']))
    home, away = np.exp(nodes).T
    line = float(market.line)
    if market.kind == 'total':
        limit = math.floor(line)
        wins = {'under': weights @ poisson.cdf(limit, home + away),
                'over': weights @ poisson.sf(limit, home + away)}
    else:
        threshold = -line
        wins = {'home': weights @ skellam.sf(math.floor(threshold), home, away),
                'away': weights @ skellam.cdf(math.ceil(threshold)-1, home, away)}
        if market.rule == 'handicap_3way':
            wins['draw'] = weights @ skellam.pmf(int(threshold), home, away)
    return {LABELS[side]: {'p_win': float(p), 'p_push': 0., 'p_loss': 1-float(p)}
            for side, p in wins.items()}


def metrics(picks):
    hits = sum(pick == actual for pick, actual, _ in picks)
    count = len(picks)
    net = sum(odds-1 if pick == actual else -1 for pick, actual, odds in picks)
    return {'n': count, 'hits': hits, 'accuracy': hits/count if count else None,
            'roi': net/count if count else None}


def baseball_key(key):
    league, kickoff, home, away = key[:4]
    names = BASEBALL_NAMES[league]
    home, away = ''.join(home.split()), ''.join(away.split())
    return league, kickoff, names.get(home, home), names.get(away, away)


def replay(products, predictions, bridge, quadrature):
    # Join K League IDs through the existing bridge. Baseball uses explicit
    # league-scoped aliases, exact kickoff, direction and an unambiguous team pair.
    by_key = defaultdict(list)
    baseball = defaultdict(list)
    for item in products:
        by_key[item[2][:4]].append(item)
        if item[2][0] in BASEBALL_NAMES:
            baseball[baseball_key(item[2])].append(item)
    bridges = defaultdict(list)
    for row in bridge:
        bridges[row['fixture_id']].append(row)
    frames = defaultdict(dict)
    for row in predictions:
        if (row['league'] not in LEAGUES or row['model'] not in MODELS or row['mode'] != 'frozen'
                or row['cohort'] != 'retrospective_year_strict__claimed_time_only'):
            continue
        key = (row['league'], clock(row['kickoff']), row['home'], row['away'], row['event_id'])
        if key[1] - clock(row['decision_at']) < timedelta(minutes=30) or row['model'] in frames[key]:
            raise ValueError('late or duplicate frozen forecast')
        frames[key][row['model']] = row['audit']
    coverage = Counter((k[0], m.kind) for _, _, k, m, *_ in products)
    evaluated = defaultdict(lambda: defaultdict(list))
    linked = defaultdict(set)
    ambiguous = Counter()
    for (*fixture, event_id), models in frames.items():
        if set(models) != set(MODELS):
            continue
        league, kickoff, home, away = fixture
        official_rows = bridges[event_id]
        if league.startswith('K리그'):
            if len(official_rows) != 1:
                continue
            official = official_rows[0]
            if kickoff != clock(official['kickoff_kst']) or [home, away] != [official['league_home_name'], official['league_away_name']]:
                continue
            key = (league, kickoff, official['betman_home_name'], official['betman_away_name'])
            offers = [r for r in by_key[key] if r[0] == str(official['gmTs']).zfill(6)
                      and r[1] == official['response_sha256'] and list(r[6]) == official['official_score']
                      and list(r[2][4:]) == [official['home_team_id'], official['away_team_id']]]
        else:
            offers = baseball[baseball_key(fixture)]
            if len({r[2][4:] for r in offers}) > 1 or len({r[6] for r in offers}) > 1:
                continue
        groups = defaultdict(list)
        for item in offers:
            groups[(item[3].kind, item[3].line)].append(item)
        for (kind, _), group in groups.items():
            if len(group) != 1:
                ambiguous[(league, kind)] += 1
                continue
            rnd, _, _, market, prices, actual, _ = group[0]
            cheapest = [side for side, odds in prices.items() if odds == min(prices.values())]
            if len(cheapest) != 1:
                continue
            options = {LABELS[side]: {'selection_id': side, 'offer_id': f'{rnd}:{market.game_no}:{side}', '배당': odds}
                       for side, odds in prices.items()}
            arms = [score_pick(name, model_masses(market, models[name], quadrature), options) for name in MODELS]
            if any(arm is None for arm in arms):
                continue
            linked[(league, kind)].add(event_id)
            for name, side in [('market', cheapest[0]), *((arm['model'], ALIASES[arm['selection']]) for arm in arms)]:
                evaluated[(league, kind)][name].append((side, actual, prices[side]))
    return {'retrospective': True, 'certified_pregame_products': 0, 'agreed_three_model_picks': None,
            'forecast_cohort': 'retrospective_year_strict__claimed_time_only',
            'forecast_kickoff_range': [min(key[1] for key in frames).isoformat(),
                                       max(key[1] for key in frames).isoformat()] if frames else None,
            'leagues': {league: {'frozen_fixtures': sum(key[0] == league and set(arms) == set(MODELS) for key, arms in frames.items()),
                **{kind: {'archive_products': coverage[(league, kind)], 'matched_fixtures': len(linked[(league, kind)]),
                          'ambiguous_line_matches': ambiguous[(league, kind)],
                          'models': {name: metrics(evaluated[(league, kind)][name]) for name in ('market', *MODELS)}}
                   for kind in ('total', 'handicap')}}
                for league in LEAGUES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', type=Path, action='append', required=True)
    parser.add_argument('--kleague-bridge', type=Path, required=True)
    args = parser.parse_args()
    hashes = json.loads((STATE / 'results/hashes.json').read_bytes())
    paths = [STATE / 'model.py', STATE / 'results/predictions.jsonl.gz']
    for path in paths:
        if sha(path.read_bytes()) != hashes[path.relative_to(ROOT).as_posix()]:
            raise ValueError('frozen score-model artifact mismatch')
    spec = importlib.util.spec_from_file_location('frozen_score_state', paths[0])
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    manifest, bridge = MANIFEST.read_bytes(), args.kleague_bridge.read_bytes()
    products = archived_products(read_rounds(json.loads(manifest), args.archive_root))
    result = replay(products, jsonl(paths[1].read_bytes()), jsonl(bridge), model.quadrature)
    result['basketball'] = replay_basketball(products)
    print(json.dumps(dict(result, manifest_sha256=sha(manifest), bridge_sha256=sha(bridge),
                          forecasts_sha256=sha(paths[1].read_bytes())), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
