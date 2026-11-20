"""New historical articles only; manually checked official scores, offline replay."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
from sport_hybrid_validation import load_data, fit, predict, SPORT_FEATURES


def key(r):
    return r['sport'], r['date'], r['home'], r['away']


def decisions(p, tip):
    p = np.asarray(p, dtype=float)
    if not np.isfinite(p).all() or (p < 0).any() or not np.isclose(p.sum(), 1) or tip not in range(len(p)):
        raise ValueError('Invalid probability/vote')
    vote = np.eye(len(p))[tip]
    return {'research_baseline': int(p.argmax()), 'tipster': tip,
            'research90_tipster10': int((.9*p + .1*vote).argmax())}


def score(rows, model):
    returns = [r['odds'][r['decisions'][model]] * (r['decisions'][model] == r['winner']) - 1 for r in rows]
    return dict(n=len(rows), hits=sum(r['decisions'][model] == r['winner'] for r in rows),
                roi_pct=100*sum(returns)/len(rows) if rows else None,
                profit_krw=round(10000*sum(returns)))


def capped(rows, model):
    days, monthly, accepted = defaultdict(list), defaultdict(int), []
    for r in rows:
        days[r['date']].append(r)
    for day, batch in sorted(days.items()):
        chosen = []
        for r in sorted(batch, key=key):
            if monthly[day[:7]] - 10000*(len(chosen)+1) >= -50000:
                chosen.append(r)
        monthly[day[:7]] += score(chosen, model)['profit_krw']
        accepted.extend(chosen)
    return dict(**score(accepted, model), monthly_net=dict(monthly),
                assumption='10000 KRW single picks; same-day losses reserved; next-day settlement proxy; no weekly cap')


def validate_new(rows, old):
    keys = [key(r) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) & {key(r) for r in old}:
        raise ValueError('Duplicate or old fixture in NEW cohort')


def run(games, sources, previous):
    manifest = json.loads(sources.read_text(encoding='utf8'))
    rows = manifest['rows']
    old = json.loads(previous.read_text(encoding='utf8'))['ledger']
    validate_new(rows, old)
    # Fit parameters on pre-2025 only. Existing feature builder walks games
    # chronologically; current/future outcomes must not enter a row's features.
    data, quality = load_data(games)
    models, training, ledger, rejected = {}, {}, [], []
    raw = list(csv.DictReader(games.open(encoding='utf8')))
    for r in rows:
        s = r['sport']
        if s not in models:
            train = data[(data.sport == s) & (data.year < 2025)]
            cols = list(dict.fromkeys(['elo_diff', *SPORT_FEATURES[s]]))
            models[s] = fit(train, cols, offset=True)
            training[s] = dict(n=len(train), last=str(train.date.max()), columns=cols)
        hit = data[(data.sport == s) & (data.league == r['league']) &
                   (data.date.astype(str).str[:10] == r['date']) &
                   (data.home_team == r['home']) & (data.away_team == r['away'])]
        if len(hit) != 1:
            rejected.append(dict(**r, reason=f'unique modeled primary offer unavailable: {len(hit)}'))
            continue
        h = hit.iloc[0]
        winner = 0 if r['score'][0] > r['score'][1] else (1 if s != 'sc' or r['score'][0] == r['score'][1] else 2)
        # Match exact local score, not merely winning side, and retain round/game provenance.
        rawhits = [x for x in raw if x['year'] == r['date'][:4] and x['date_text'].startswith(r['date'][5:].replace('-', '.'))
                   and x['sport'] == s and x['league'] == r['league']
                   and x['market_family'] == ('승무패' if s == 'sc' else '승패')
                   and x['home'] == r['home']+' '+str(r['score'][0])
                   and x['away'] == str(r['score'][1])+' '+r['away']
                   and np.allclose([float(z) for z in x['odds'].split(',')], h.odds)]
        if int(h.y) != winner or not rawhits:
            raise ValueError('Official score/CSV conflict: '+str(key(r)))
        p = predict(models[s], hit)[0].tolist()
        ledger.append(dict(**r, kickoff_kst=str(h.kickoff), odds=list(h.odds), probability=p,
                           winner=winner, decisions=decisions(p, r['tip']),
                           price_rows=[{k:x[k] for k in ('year','round','game_no','date_text','odds')} for x in rawhits]))
    names = ('research_baseline','tipster','research90_tipster10')
    groups = {s:[r for r in ledger if r['sport']==s] for s in ('bs','bk','sc','vl')}
    return dict(status='retrospective convenience sample, not adoption evidence', new_articles=len(rows),
                joined_new=len(ledger), old_fixture_overlap=0, strict_pregame_eligible=0,
                definitions={'baseline':'2023-2024 trained market-offset logistic research reconstruction, NOT frozen production predictions',
                             'hybrid':'0.9 research probability + 0.1 single-author one-hot vote; argmax; not logit blend',
                             'odds':'archived actual quoted prices; quote time and executable availability unproven',
                             'results':'official sources manually read; exact scores checked against historical CSV',
                             'timing':manifest['timing'],'selection':manifest['selection']},
                hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (games,sources,previous)},
                training=training, source_quality=quality, rejected=rejected,
                sports={s:{m:score(g,m) for m in names} for s,g in groups.items()},
                changed_picks={s:sum(r['decisions'][names[0]] != r['decisions'][names[2]] for r in g) for s,g in groups.items()},
                article_only_sensitivity={m:score([r for r in ledger if r['evidence']=='indexed_article' and not r.get('note')],m) for m in names},
                monthly_cap={m:capped(ledger,m) for m in names}, ledger=ledger)


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('games','sources','previous','output'): ap.add_argument('--'+name, type=Path, required=True)
    a=ap.parse_args(); result=run(a.games,a.sources,a.previous)
    with a.output.open('x',encoding='utf8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('joined_new','rejected','sports','changed_picks','monthly_cap')},ensure_ascii=False))
