"""Fit the K League team Poisson design to 2025 scores; replay 2026 handicaps."""
import argparse
from collections import Counter, defaultdict
from datetime import date
import importlib.util
import json
from math import log
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from league_handicap import poisson_rates
from market_outcomes import probabilities
from market_pick_frontier import score_pick
from public_tipster_votes import kickoff

TRAIN_THROUGH = date(2025, 12, 31)
TUNE_THROUGH = date(2025, 9, 30)
RIDGE = 100
MIN_TRAIN = 100
MIN_EVAL = 40
COMPETITIONS = {
    '52': ('EPL', '잉글랜드 프리미어리그'),
    '67': ('라리가', '스페인 라리가', '스페인라리가'),
    '56': ('분데스리', '독일 분데스리가'),
    '53': ('세리에A', '이탈리아 세리에A'),
    '54': ('프리그1', '프랑스 리그1'),
    '2': ('에레디비', '네덜란드 에레디비시'),
    'SC021': ('UCL', 'UEFA 챔피언스리그'),
    'SC047': ('UEL', 'UEFA 유로파리그'),
    'SC107': ('UECL', 'UEFA 컨퍼런스리그'),
    '130': ('MLS', '미국 메이저리그사커', '미국메이저리그사커'),
    'SC007': ('J1리그', '일본 J1리그'),
    'd0': ('ACLE', 'AFC 챔피언스리그 엘리트'),
    'd1': ('ACL2', 'AFC 챔피언스리그2'),
    'da': ('코파 리베르타도레스',),
    'SC099': ('축구 월드컵',),
    'SC042': ('축월드예',),
    'SC061': ('UEFA 네이션스리그',),
    'SC100': ('남축INTL', '남자축구 국제친선경기'),
}
SIDES = {'home': '핸디홈', 'draw': '핸디무', 'away': '핸디원정'}
BY_LABEL = {label: side for side, label in SIDES.items()}


def load_archive(script, roots):
    spec = importlib.util.spec_from_file_location('archived_markets', script)
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)
    manifest = archive.MANIFEST.read_bytes()
    for names in COMPETITIONS.values():
        archive.LEAGUES[names[0]] = 'sc'
    rounds = []
    for number, digest, rows in archive.read_rounds(json.loads(manifest), roots):
        relevant = []
        for row in rows:
            names = COMPETITIONS.get(str(row.get('leagueCode')), ())
            if str(row.get('itemCode')).lower() == 'sc' and row.get('leagueName') in names:
                relevant.append(dict(row, leagueName=names[0]))
        rounds.append((number, digest, relevant))
    products = [row for row in archive.archived_products(rounds) if row[3].kind == 'handicap']
    return products, archive.sha(manifest)


def fit(fixtures, cutoff=TRAIN_THROUGH):
    teams = {team: i for i, team in enumerate(sorted({t for key in fixtures for t in key[1:]}))}
    design = np.zeros((2 * len(fixtures), 2 + 2 * len(teams)))
    goals = []
    weights = []
    for index, ((kickoff, home, away), score) in enumerate(sorted(fixtures.items())):
        goals.extend(score)
        age = (cutoff - kickoff.date()).days
        for side, (own, opponent) in enumerate(((home, away), (away, home))):
            row = design[2 * index + side]
            row[[0, 2 + teams[own], 2 + len(teams) + teams[opponent]]] = 1
            row[1] = side == 0
            weights.append(2 ** (-age / 180))
    goals, weights = np.asarray(goals), np.asarray(weights)

    def objective(coefficients):
        log_rate = design @ coefficients
        if np.max(np.abs(log_rate)) > 30:
            return 1e50, np.ones_like(coefficients) * 1e20
        rate = np.exp(log_rate)
        penalty = coefficients.copy()
        penalty[0] = 0
        loss = weights @ (rate - goals * log_rate) + .5 * RIDGE * (penalty @ penalty)
        gradient = design.T @ (weights * (rate - goals)) + RIDGE * penalty
        return loss, gradient

    result = minimize(objective, np.zeros(design.shape[1]), jac=True, method='L-BFGS-B',
                      options={'maxiter': 3000, 'ftol': 1e-12, 'gtol': 1e-7})
    if not result.success:
        raise RuntimeError(result.message)
    return {'teams': teams, 'coefficients': result.x.tolist()}


def offer_inputs(row, fitted):
    _, _, (league, _, _, _, home, away), market, prices, result, _ = row
    offers = {SIDES[side]: {'selection_id': side, 'offer_id': side, '배당': price}
              for side, price in prices.items()}
    terms = dict(game_no=market.game_no, market=market.kind, line=market.line, period=market.period,
                 settlement_rule=market.rule, n_way=3, sport='sc', league=league, unit='goals')
    masses = probabilities(terms, *poisson_rates(fitted, str(home), str(away)))
    inverse = {side: 1/offer['배당'] for side, offer in offers.items()}
    market_p = {side: value/sum(inverse.values()) for side, value in inverse.items()}
    return offers, masses, market_p, result


def tuned_weight(train, products_at_line):
    """Choose only on Oct–Dec 2025, using a model fitted through September."""
    initial = {key: score for key, score in train.items() if key[0].date() <= TUNE_THROUGH}
    if len(initial) < 60:
        return 0., 0
    model = fit(initial, TUNE_THROUGH)
    weights = (0., .25, .5, .75, 1.)
    losses = {weight: 0. for weight in weights}
    common = set()
    for (key, _), rows in products_at_line.items():
        if (len(rows) != 1 or key not in train or key in initial
                or any(team not in model['teams'] for team in key[1:])):
            continue
        _, masses, market_p, outcome = offer_inputs(rows[0], model)
        side = SIDES[outcome]
        common.add(key)
        for weight in weights:
            p = (1-weight)*market_p[side] + weight*masses[side]['p_win']
            losses[weight] -= log(max(1e-12, p))
    return (min(weights, key=losses.get) if len(common) >= 30 else 0.), len(common)


def published_aliases(products, published):
    """Use the same round, handicap product, kickoff and line to link display names."""
    indexed = defaultdict(list)
    for row in products:
        number, _, key, market, _, _, _ = row
        indexed[(number, key[0], int(market.game_no))].append(row)
    aliases = defaultdict(lambda: defaultdict(set))
    for game in published['live'] + published['past']:
        if game.get('sport') != 'sc':
            continue
        try:
            number = f"{int(game['year']) % 100:02d}{int(game['round']):04d}"
            start = kickoff(game)
        except (KeyError, TypeError, ValueError):
            continue
        for option in game.get('options', []):
            if option.get('market') != '핸디캡':
                continue
            try:
                matches = indexed[(number, game['league'], int(option['게임번호']))]
                if len(matches) != 1 or matches[0][2][1] != start or float(matches[0][3].line) != float(option['line']):
                    continue
                for field, identity in zip(('home', 'away'), matches[0][2][4:]):
                    aliases[game['league']][''.join(game[field].split())].add(str(identity))
            except (KeyError, TypeError, ValueError):
                continue
    return aliases


def replay(products, published):
    aliases_from_site = published_aliases(products, published)
    by_league = defaultdict(list)
    for row in products:
        by_league[row[2][0]].append(row)
    models, report = {}, {}
    for names in COMPETITIONS.values():
        league = names[0]
        entries = by_league[league]
        scores, products_at_line, team_aliases = defaultdict(set), defaultdict(list), defaultdict(set)
        for row in entries:
            _, _, (_, kickoff, home_name, away_name, home, away), market, _, _, score = row
            key = kickoff, str(home), str(away)
            scores[key].add(score)
            products_at_line[(key, market.line)].append(row)
            for name, identity in ((home_name, home), (away_name, away)):
                team_aliases[''.join(name.split())].add(str(identity))
        train = {key: next(iter(score)) for key, score in scores.items()
                 if key[0].year == 2025 and len(score) == 1}
        evaluation = {key for key, score in scores.items() if key[0].year == 2026 and len(score) == 1}
        counts = Counter()
        summary = report[league] = {'training_fixtures': len(train), 'archive_evaluation_fixtures': len(evaluation),
                                    'status': 'insufficient_training'}
        if len(train) < MIN_TRAIN:
            continue
        fitted = fit(train)
        weight, tune_count = tuned_weight(train, products_at_line)
        common = set()
        net = dict(model=0., market=0., blend=0.)
        hits = dict(model=0, market=0, blend=0)
        for (key, _), members in sorted(products_at_line.items()):
            if key not in evaluation:
                continue
            if len(members) != 1:
                counts['ambiguous_round_or_line'] += 1
                continue
            if any(team not in fitted['teams'] for team in key[1:]):
                counts['unseen_team'] += 1
                continue
            _, _, _, _, prices, result, _ = members[0]
            favourite = [side for side, price in prices.items() if price == min(prices.values())]
            if len(favourite) != 1:
                counts['tied_market'] += 1
                continue
            offers, masses, market_p, _ = offer_inputs(members[0], fitted)
            arm = score_pick('team_poisson', masses, offers)
            blend = {}
            for side, mass in masses.items():
                win = (1-weight)*market_p[side] + weight*mass['p_win']
                blend[side] = {'p_win': win, 'p_push': 0., 'p_loss': 1-win}
            blended = score_pick('blend', blend, offers)
            if arm is None or blended is None:
                counts['tied_model'] += 1
                continue
            common.add(key)
            counts['products'] += 1
            for model, side in (('model', BY_LABEL[arm['selection']]), ('market', favourite[0]),
                                ('blend', BY_LABEL[blended['selection']])):
                hit = side == result
                hits[model] += hit
                net[model] += prices[side] - 1 if hit else -1
        count = counts['products']
        metrics = {name: {'hits': hits[name], 'accuracy': hits[name]/count if count else None,
                          'roi': net[name]/count if count else None} for name in net}
        better = len(common) >= MIN_EVAL and weight > 0 and net['blend'] > net['market'] and hits['blend'] >= hits['market']
        summary.update(common_fixtures=len(common), product_counts=dict(counts), metrics=metrics,
                       blend_model_weight=weight, tuning_fixtures=tune_count,
                       status='candidate' if better else 'insufficient_evaluation' if len(common) < MIN_EVAL else 'no_switch')
        if len(common) >= MIN_EVAL:
            for name, ids in aliases_from_site[league].items():
                team_aliases[name].update(ids)
            models[league] = dict(fitted, league_names=list(names), train_through=TRAIN_THROUGH.isoformat(),
                                  aliases={name: next(iter(ids)) for name, ids in team_aliases.items()
                                           if name and '�' not in name and len(ids) == 1 and next(iter(ids)) in fitted['teams']})
            summary['mapped_teams'] = len(set(models[league]['aliases'].values()))
            summary['site_aliases'] = sum(name in models[league]['aliases'] for name in aliases_from_site[league])
    return models, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-replay', required=True, type=Path)
    parser.add_argument('--archive-root', required=True, action='append', type=Path)
    parser.add_argument('--published-picks', required=True, type=Path)
    args = parser.parse_args()
    products, digest = load_archive(args.archive_replay, args.archive_root)
    published = args.published_picks.read_bytes()
    models, report = replay(products, json.loads(published))
    import hashlib
    print(json.dumps({'archive_manifest_sha256': digest, 'published_picks_sha256': hashlib.sha256(published).hexdigest(),
                      'retrospective': True,
                      'models': models, 'report': report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
