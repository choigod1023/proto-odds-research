"""Offline league-specific score GLMs. No production imports except odds devig.

Selection: 2023 fit -> 2024 validation, then yearly refits using only prior years.
Historical records are retrospective archives, NOT certified pregame snapshots.
"""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
from collections import Counter, defaultdict, deque
from datetime import date, timedelta
from itertools import groupby, product
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import digamma, expit, gammaln, logit
from scipy.stats import nbinom, poisson

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from devig import market_probabilities

LEAGUES = ('KBO', 'NPB', 'MLB')
FEATURES = {
    'core': ['attack', 'opponent_defense', 'home'],
    'schedule': ['rest', 'opponent_rest', 'games3', 'opponent_games3'],
    'rotation': ['starter_team_ra_proxy', 'starter_known', 'starter_rest'],
    'rich': ['starter_ra9', 'starter_kbb_proxy', 'starter_outs', 'pitch_detail_known',
             'bullpen_ra9', 'bullpen_outs3', 'lineup_obp_proxy', 'lineup_slug_proxy',
             'lineup_log5_proxy', 'lineup_known'],
}


def columns(bundle):
    keys = ['core', 'schedule', 'rotation', 'rich']
    return [n for key in keys[:keys.index(bundle)+1] for n in FEATURES[key]]


def innings_outs(value):
    """Naver fractional innings, including thirds, to integer outs."""
    text = str(value).strip()
    fraction = 1 if '⅓' in text else 2 if '⅔' in text else 0
    text = text.replace('⅓', '').replace('⅔', '').strip()
    if '.' in text:
        whole, tail = text.split('.')
        if tail not in ('0', '1', '2'):
            raise ValueError('invalid baseball innings')
        return 3*int(whole)+int(tail)
    return 3*int(text or '0')+fraction


def key(r):
    return r['league'], r['date'], r['home'], r['away']


class PastState:
    def __init__(self, league, window):
        self.league, self.window = league, window
        self.teams = defaultdict(lambda: deque(maxlen=60))
        self.pitch = defaultdict(lambda: np.zeros(6))  # outs,r,bb,k,h,proxy BF
        self.pitch_dates = {}
        self.pitch_starts = Counter()
        self.last_starts = {}
        self.start_intervals = deque(maxlen=500)
        self.bat = defaultdict(lambda: np.zeros(6))  # ab,h,bb,hr,2b,3b
        self.lineups = defaultdict(lambda: deque(maxlen=3))
        self.bull = defaultdict(lambda: deque(maxlen=80))
        self.total_runs = self.total_sides = 0
        self.bat_total = np.zeros(6)

    def mean_runs(self):
        prior = {'KBO': 4.5, 'NPB': 3.8, 'MLB': 4.4}[self.league]
        return (self.total_runs+20*prior)/(self.total_sides+20)

    def projected_starter(self, team, day):
        recent = list(self.teams[team])[-10:]
        names = {}
        for r in recent:
            if r['starter']:
                names[r['starter']] = r
        candidates = []
        # Calendar days between starts, not number of pitchers in a rotation.
        # Infer from PREVIOUS starts; never tune on the current fixture's starter.
        ideal = float(np.median(self.start_intervals)) if len(self.start_intervals) >= 20 else (6 if self.league == 'NPB' else 5)
        for name, r in names.items():
            rest = (day-r['day']).days
            if 3 <= rest <= 30:
                candidates.append((abs(rest-ideal), -r['day'].toordinal(), name, rest))
        if not candidates:
            return '', 0
        _, _, name, rest = min(candidates)
        return name, rest

    def offense(self, team, opponent, day, home):
        avg = self.mean_runs()
        own, opp = list(self.teams[team]), list(self.teams[opponent])
        a, d = own[-self.window:], opp[-self.window:]
        rate = lambda rs, field: (sum(r[field] for r in rs)+10*avg)/(len(rs)+10)
        name, starter_rest = self.projected_starter(opponent, day)
        starter_games = [r for r in opp if name and r['starter'] == name]
        starter_ra = (sum(r['ga'] for r in starter_games)+5*avg)/(len(starter_games)+5)
        rest = lambda rs: min(7, (day-rs[-1]['day']).days) if rs else 7
        games3 = lambda rs: sum(0 < (day-r['day']).days <= 3 for r in rs)
        f = dict(attack=math.log(rate(a, 'gf')/avg),
                 opponent_defense=math.log(rate(d, 'ga')/avg), home=int(home),
                 rest=rest(own), opponent_rest=rest(opp), games3=games3(own),
                 opponent_games3=games3(opp), starter_team_ra_proxy=math.log(starter_ra/avg),
                 starter_known=int(bool(name)), starter_rest=min(starter_rest, 14))
        meta = dict(projected_starter=name, projected_starter_rest=starter_rest,
                    latest_team_date=own[-1]['day'].isoformat() if own else None)
        if self.league != 'KBO':
            return f, meta
        pk = (opponent, name)
        fresh_pitch = bool(name and pk in self.pitch_dates and (day-self.pitch_dates[pk]).days <= 14)
        ps = self.pitch[pk] if fresh_pitch else np.zeros(6)
        ra9 = (27*ps[1]+135*avg)/(ps[0]+135)
        # Raw 'bf' appears to be pitch count, not BF. Do NOT use it as a denominator.
        # outs+hits+walks is only an opportunity proxy; excludes HBP/errors/DP detail.
        kbb = (ps[3]-ps[2]+100*.12)/(ps[5]+100)
        bp = [r for r in self.bull[opponent] if 0 < (day-r[0]).days <= 60]
        bp_out = sum(r[1] for r in bp)
        bp_ra = (27*sum(r[2] for r in bp)+135*avg)/(bp_out+135)
        bp_recent = sum(r[1] for r in bp if (day-r[0]).days <= 3)
        lineups = [r for r in self.lineups[team] if (day-r[0]).days <= 14]
        order = defaultdict(Counter)
        for _, lineup in lineups:
            for slot, code in lineup.items():
                order[slot][code] += 1
        ids = []
        for slot in range(1, 10):
            available = sorted(order[slot], key=lambda c: (-order[slot][c], c))
            ids.extend(available[:1] if available and available[0] not in ids else [])
        league_obp = (self.bat_total[1]+self.bat_total[2]+100*.32)/(self.bat_total[0]+self.bat_total[2]+100)
        league_slg = (self.bat_total[1]+self.bat_total[4]+2*self.bat_total[5]+3*self.bat_total[3]+100*.4)/(self.bat_total[0]+100)
        obps, slgs = [], []
        for code in ids:
            b = self.bat[code]
            obps.append((b[1]+b[2]+150*league_obp)/(b[0]+b[2]+150))
            slgs.append((b[1]+b[4]+2*b[5]+3*b[3]+150*league_slg)/(b[0]+150))
        obp = sum(obps+[league_obp]*(9-len(ids)))/9
        slg = sum(slgs+[league_slg]*(9-len(ids)))/9
        allowed = (ps[4]+ps[2]+100*league_obp)/(ps[5]+100)
        proxy = lambda p: float(logit(np.clip(p, .02, .98)))
        f.update(starter_ra9=math.log(ra9/avg), starter_kbb_proxy=kbb,
                 starter_outs=float(ps[0]/max(1, self.pitch_starts[pk])) if fresh_pitch else 0.,
                 pitch_detail_known=int(fresh_pitch), bullpen_ra9=math.log(bp_ra/avg),
                 bullpen_outs3=bp_recent, lineup_obp_proxy=obp-league_obp,
                 lineup_slug_proxy=slg-league_slg,
                 lineup_log5_proxy=proxy(obp)+proxy(allowed)-2*proxy(league_obp),
                 lineup_known=len(ids)/9)
        meta.update(pitch_detail_known=fresh_pitch, lineup_players=len(ids),
                    latest_lineup_date=lineups[-1][0].isoformat() if lineups else None)
        return f, meta

    def update(self, r):
        day = date.fromisoformat(r['date'])
        for side, team, gf, ga in [(0, r['home'], r['hg'], r['ag']), (1, r['away'], r['ag'], r['hg'])]:
            starter = (r.get('actual_starters') or ['', ''])[side] or ''
            pitchers = (r.get('pitchers') or [[], []])[side]
            if pitchers:
                starter = pitchers[0][0] or starter
                p = pitchers[0]
                outs = innings_outs(p[1])
                self.pitch[(team, starter)] += [outs, p[2], p[3], p[4], p[5], outs+p[5]+p[3]]
                self.pitch_dates[(team, starter)] = day
                self.pitch_starts[(team, starter)] += 1
                self.bull[team].append((day, sum(innings_outs(p[1]) for p in pitchers[1:]),
                                        sum(p[2] for p in pitchers[1:])))
            if starter:
                previous=self.last_starts.get((team,starter))
                if previous and 3 <= (day-previous).days <= 14:
                    self.start_intervals.append((day-previous).days)
                self.last_starts[(team,starter)]=day
            self.teams[team].append(dict(day=day, gf=gf, ga=ga, starter=starter))
            self.total_runs += gf
            self.total_sides += 1
            batters = (r.get('batters') or [[], []])[side]
            lineup = {}
            for b in batters:
                if not b[0]:
                    continue
                self.bat[b[0]] += b[2:]
                self.bat_total += b[2:]
                if 1 <= b[1] <= 9:
                    lineup.setdefault(b[1], b[0])
            if lineup:
                self.lineups[team].append((day, lineup))


def build_features(history, league, window):
    """Snapshot all fixtures for a date BEFORE updating any that date."""
    state = PastState(league, window)
    out = []
    for day, batch in groupby(sorted((r for r in history if r['league'] == league), key=key), lambda r: r['date']):
        rows = list(batch)
        for r in rows:
            f, m = state.offense(r['home'], r['away'], date.fromisoformat(day), True)
            a, am = state.offense(r['away'], r['home'], date.fromisoformat(day), False)
            out.append(dict(row=r, features=[f, a], metadata=[m, am]))
        for r in rows:
            state.update(r)
    return out


def loss_gradient(theta, x, y, weights, distribution):
    beta = theta[:-1] if distribution == 'nb' else theta
    eta = x@beta
    mu = np.exp(np.clip(eta, -2, math.log(15)))
    active = (eta > -2) & (eta < math.log(15))
    ridge = .03*np.sum(beta[1:]**2)/2
    penalty = np.r_[0., .03*beta[1:]]
    if distribution == 'poisson':
        ll = y*np.log(mu)-mu-gammaln(y+1)
        return float(-np.dot(weights, ll)+ridge), x.T@(weights*(mu-y)*active)+penalty
    r = np.exp(theta[-1])
    ll = gammaln(y+r)-gammaln(r)-gammaln(y+1)+r*np.log(r/(r+mu))+y*np.log(mu/(r+mu))
    grad_beta = x.T@(weights*r*(mu-y)/(r+mu)*active)+penalty
    dl_dr = digamma(y+r)-digamma(r)+np.log(r/(r+mu))+1-(r+y)/(r+mu)
    prior = theta[-1]-math.log(5)
    return float(-np.dot(weights, ll)+ridge+.001*prior**2/2), np.r_[grad_beta, -np.dot(weights, dl_dr)*r+.001*prior]


def decay_weights(days, half_life):
    weights = np.ones(len(days)) if half_life == 0 else np.exp2(-(max(days)-days)/half_life)
    return weights/weights.sum()


def fit_model(rows, names, distribution, half_life, cutoff):
    train = [r for r in rows if r['row']['date'][:4] <= str(cutoff)]
    raw = np.array([[f[n] for n in names] for r in train for f in r['features']])
    y = np.array([v for r in train for v in (r['row']['hg'], r['row']['ag'])])
    days = np.array([date.fromisoformat(r['row']['date']).toordinal() for r in train for _ in range(2)])
    center, scale = raw.mean(axis=0), raw.std(axis=0)
    scale[scale < 1e-8] = 1
    x = np.c_[np.ones(len(y)), np.clip((raw-center)/scale, -6, 6)]
    init = np.r_[math.log(max(y.mean(), .2)), np.zeros(len(names))]
    bounds = [(-2, math.log(15))]+[(-5, 5)]*len(names)
    if distribution == 'nb':
        init = np.r_[init, math.log(5)]
        bounds += [(math.log(.1), math.log(1000))]
    fit = minimize(loss_gradient, init, args=(x, y, decay_weights(days, half_life), distribution),
                   method='L-BFGS-B', jac=True, bounds=bounds, options={'maxiter': 350, 'ftol': 1e-11, 'gtol': 1e-7})
    if not fit.success:
        raise RuntimeError(str(fit.message))
    return dict(names=names, distribution=distribution, half_life=half_life, cutoff=cutoff,
                center=center.tolist(), scale=scale.tolist(), theta=fit.x.tolist(),
                train_games=len(train), train_last_date=train[-1]['row']['date'], loss=float(fit.fun),
                iterations=int(fit.nit), success=bool(fit.success))


def win_prob(mu_home, mu_away, distribution, shape=None):
    dists = [poisson(m) if distribution == 'poisson' else nbinom(shape, shape/(shape+m)) for m in (mu_home, mu_away)]
    end = max(int(d.ppf(1-1e-11)) for d in dists)
    if end > 1000:
        raise ValueError('score tail too wide')
    support = np.arange(end+1)
    ph, pa = [d.pmf(support) for d in dists]
    home = float(np.dot(ph, np.r_[0., np.cumsum(pa)[:-1]]))
    away = float(np.dot(pa, np.r_[0., np.cumsum(ph)[:-1]]))
    return home/(home+away)


def predict(model, feature_rows):
    raw = np.array([[f[n] for n in model['names']] for r in feature_rows for f in r['features']])
    x = np.c_[np.ones(len(raw)), np.clip((raw-np.array(model['center']))/np.array(model['scale']), -6, 6)]
    beta = model['theta'][:-1] if model['distribution'] == 'nb' else model['theta']
    means = np.exp(np.clip(x@beta, -2, math.log(15))).reshape(-1, 2)
    shape = math.exp(model['theta'][-1]) if model['distribution'] == 'nb' else None
    return np.array([win_prob(h, a, model['distribution'], shape) for h, a in means])


def measure(rows, probabilities=None, picks=None):
    if picks is None:
        picks = ['home' if p >= .5 else 'away' for p in probabilities]
    wins = losses = pushes = 0
    profits, prices = [], []
    labels, probs = [], []
    for i, (r, pick) in enumerate(zip(rows, picks)):
        draw = r['hg'] == r['ag']
        winner = 'home' if r['hg'] > r['ag'] else 'away'
        wins += int(not draw and pick == winner)
        losses += int(not draw and pick != winner)
        pushes += int(draw)
        if 'prices' in r:
            price = r['prices'][r['order'].index(pick)]
            net = 0 if draw else price-1 if pick == winner else -1
            profits.append(net)
            prices.append(price)
        if probabilities is not None and not draw:
            labels.append(int(winner == 'home'))
            probs.append(probabilities[i])
    result = dict(n=len(rows), wins=wins, losses=losses, pushes=pushes,
                  accuracy=wins/(wins+losses) if wins+losses else None)
    if profits:
        months = defaultdict(float)
        for r, net in zip(rows, profits):
            months[r['date'][:7]] += net
        result.update(roi=sum(profits)/len(rows), net_krw=round(sum(profits)*10000),
                      mean_odds=float(np.mean(prices)), months=dict(months),
                      worst_observed_month_units=min(months.values()))
    if labels:
        p, y = np.clip(probs, 1e-9, 1-1e-9), np.array(labels)
        result.update(brier=float(np.mean((p-y)**2)), logloss=float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))))
    return result


def select(validation):
    eligible = [c for c,v in validation.items() if v.get('eligible',True)]
    hit = min(eligible, key=lambda c: (-validation[c]['accuracy'], validation[c]['brier'], c))
    brier = min(eligible, key=lambda c: (validation[c]['brier'], -validation[c]['accuracy'], c))
    return dict(hit=hit, brier=brier)


def bootstrap_delta(rows, p, reference, repetitions=3000):
    groups = defaultdict(lambda: np.zeros(3))
    for r, a, b in zip(rows, p, reference):
        day = date.fromisoformat(r['date'])
        week = (day-timedelta(days=day.weekday())).isoformat()
        draw = r['hg'] == r['ag']
        win = 'home' if r['hg'] > r['ag'] else 'away'
        picks = ['home' if t >= .5 else 'away' for t in [a, b]]
        outcomes = [int(s == win and not draw) for s in picks]
        net = [0 if draw else r['prices'][r['order'].index(s)]-1 if s == win else -1 for s in picks]
        groups[week] += [outcomes[0]-outcomes[1], net[0]-net[1], 1]
    data = np.array(list(groups.values()))
    if len(data) < 2:
        return dict(observed_weeks=len(data))
    rng = np.random.default_rng(20261029)
    summed = data[rng.integers(len(data), size=(repetitions, len(data)))].sum(axis=1)
    return dict(observed_weeks=len(data), repetitions=repetitions,
                accuracy_delta_95=np.quantile(summed[:, 0]/summed[:, 2], [.025, .975]).tolist(),
                roi_delta_95=np.quantile(summed[:, 1]/summed[:, 2], [.025, .975]).tolist())


def read_rows(path):
    return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def run(folder):
    history, targets = read_rows(folder/'history.jsonl'), read_rows(folder/'targets.jsonl')
    report = dict(selection='2023 fit, 2024 validation, prior-year refits; retrospective reused target cohort',
                  monthly_cap_in_research=False, fit_catalog={}, leagues={})
    audit = []
    for league in LEAGUES:
        cohort = sorted([r for r in targets if r['league'] == league], key=key)
        needed = {key(r) for r in cohort}
        market = np.array([market_probabilities(r['prices'])[r['order'].index('home')] for r in cohort])
        votes = np.array([r['vote_counts'].get('home', 0)/r['voters'] for r in cohort])
        reference = .9*market+.1*votes
        baselines = {n: measure(cohort, p) for n, p in [('market', market), ('market90_vote10', reference), ('tipster_vote', votes)]}
        # Confirm this is exactly the same fixed comparison, not a silently changed cohort.
        for n, ps in [('market', market), ('market90_vote10', reference), ('tipster_vote', votes)]:
            assert all(('home' if p >= .5 else 'away') == r['picks'][n] for r, p in zip(cohort, ps))
        predictions, validation, evaluation = {}, {}, {}
        target_meta = {}
        bundles = ('core', 'schedule', 'rotation', 'rich') if league == 'KBO' else ('core', 'schedule', 'rotation')
        for window in (20, 60):
            features = build_features(history, league, window)
            val = [r for r in features if r['row']['date'].startswith('2024')]
            target_rows = {key(r['row']): r for r in features if key(r['row']) in needed}
            ordered = [target_rows[key(r)] for r in cohort]
            target_meta[window] = [r['metadata'] for r in ordered]
            for bundle, distribution, half in product(bundles, ('poisson', 'nb'), (0, 90, 180)):
                name = f'{bundle}-{distribution}-w{window}-h{half}'
                fit = fit_model(features, columns(bundle), distribution, half, 2023)
                validation[name] = measure([r['row'] for r in val], predict(fit, val))
                models = {'2023': fit}
                probs = np.zeros(len(cohort))
                for year in sorted({int(r['date'][:4]) for r in cohort}):
                    cutoff = year-1
                    models[str(cutoff)] = fit_model(features, columns(bundle), distribution, half, cutoff)
                    indices = [i for i, r in enumerate(cohort) if int(r['date'][:4]) == year]
                    probs[indices] = predict(models[str(cutoff)], [ordered[i] for i in indices])
                eligible = 'starter_known' not in fit['names'] or fit['center'][fit['names'].index('starter_known')] > 0
                validation[name]['eligible']=eligible
                if not eligible:validation[name]['exclusion']='No observed starter identities in 2023 training; cannot validate rotation features'
                predictions[name] = probs
                evaluation[name] = measure(cohort, probs)
                report['fit_catalog'][league+':'+name] = models
            print(f'{league} window{window}: {len(validation)} candidates done', flush=True)
        chosen = select(validation)
        policies = dict(baselines)
        intervals = {}
        for reason, name in chosen.items():
            p = predictions[name]
            for suffix, mixed in [('pure', p), ('market90_new10', .9*market+.1*p), ('existing91_90_new10', .9*reference+.1*p)]:
                label = reason+':'+suffix
                policies[label] = measure(cohort, mixed)
                intervals[label] = dict(vs_market=bootstrap_delta(cohort, mixed, market),
                                        vs_existing91=bootstrap_delta(cohort, mixed, reference))
        primary = predictions[chosen['hit']]
        changed = [(r, p) for r, p, m in zip(cohort, primary, market) if (p >= .5) != (m >= .5)]
        diagnostics = dict(
            market_disagreements=len(changed),
            new_wins_when_disagree=sum(int((p >= .5) == (r['hg'] > r['ag'])) for r, p in changed),
            one_run=measure([r for r in cohort if abs(r['hg']-r['ag']) == 1],
                            primary[[abs(r['hg']-r['ag']) == 1 for r in cohort]]),
            other_margin=measure([r for r in cohort if abs(r['hg']-r['ag']) != 1],
                            primary[[abs(r['hg']-r['ag']) != 1 for r in cohort]]),
            calibration=[])
        for low, high in [(.5, .55), (.55, .6), (.6, .7), (.7, 1.000001)]:
            indices = [i for i, p in enumerate(primary) if low <= max(p, 1-p) < high]
            diagnostics['calibration'].append(dict(low=low, high=high, n=len(indices),
                mean_confidence=float(np.mean([max(primary[i], 1-primary[i]) for i in indices])) if indices else None,
                observed=measure([cohort[i] for i in indices], primary[indices])))
        window = int(chosen['hit'].split('-w')[1].split('-')[0])
        meta = target_meta[window]
        for r, ms in zip(cohort, meta):
            for i, m in enumerate(ms):
                # home offense uses OPPONENT starter.
                actual = (next(h for h in history if key(h) == key(r)).get('actual_starters') or ['', ''])[1-i]
                audit.append(dict(league=league, fixture_id=r['fixture_id'], date=r['date'], offense=r['home' if i == 0 else 'away'],
                    actual_opponent_starter_for_diagnostic_only=actual, **m))
        known = [a for a in audit if a['league'] == league and a['actual_opponent_starter_for_diagnostic_only']]
        diagnostics['rotation_identity'] = dict(actual_known=len(known),
            predicted_known=sum(bool(a['projected_starter']) for a in known),
            exact_matches=sum(a['projected_starter'] == a['actual_opponent_starter_for_diagnostic_only'] for a in known))
        if league == 'KBO':
            diagnostics['rich_input_coverage'] = dict(sides=len(meta)*2,
                fresh_pitch=sum(m['pitch_detail_known'] for ms in meta for m in ms),
                full_projected_lineup=sum(m['lineup_players'] == 9 for ms in meta for m in ms))
        report['leagues'][league] = dict(date_start=cohort[0]['date'], date_end=cohort[-1]['date'], selected=chosen,
            validation=validation, all_candidate_evaluation=evaluation, policies=policies,
            intervals=intervals, diagnostics=diagnostics)
        for i, r in enumerate(cohort):
            r['new_probabilities'] = {n: float(p[i]) for n, p in predictions.items()}
            r['market_home_probability'] = float(market[i])
            r['existing91_home_probability'] = float(reference[i])
        # Keep target order and every candidate's unrounded probability for independent settlement.
    report['input_sha256'] = {n: hashlib.sha256((folder/n).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for n in ['history.jsonl', 'targets.jsonl']}
    for name, value in [('results.json', report)]:
        (folder/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8', newline='\n')
    for name, rs in [('predictions.jsonl', targets), ('feature-audit.jsonl', audit)]:
        (folder/name).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rs), encoding='utf8', newline='\n')
    print(json.dumps({l: r['policies'] for l, r in report['leagues'].items()}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder', type=Path, required=True)
    run(p.parse_args().folder)
