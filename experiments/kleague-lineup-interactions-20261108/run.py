"""Match-level XI contribution, not possession RAPM or proven pregame lineups."""
from __future__ import annotations

import os
for name in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[name] = '1'

import argparse
import gzip
import hashlib
import heapq
import itertools
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax
from scipy.sparse import csr_matrix
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ALIASES = {'FC서울': '서울', 'FC안양': '안양', '강원FC': '강원', '광주FC': '광주',
           '김천상무': '김천', '대구FC': '대구', '대전하나': '대전', '부천FC': '부천',
           '수원FC': '수원FC', '수원삼성': '수원', '울산HDFC': '울산', '인천유나': '인천',
           '전북현대': '전북', '제주SKFC': '제주', '포항스틸': '포항'}


def starting_xi(game, side):
    players = game['data'][side]['players']
    ids = [str(p['playerId']) for p in players]
    orders = [int(p['positionOrder']) for p in players]
    if len(ids) != 11 or len(set(ids)) != 11 or sorted(orders) != list(range(1, 12)):
        raise ValueError('Not a unique starting XI')
    return sorted(ids)


def connect(raw, prices):
    index = defaultdict(list)
    for g in raw.values():
        index[(g['date'], g['home'], g['away'])].append(g)
    output, exclusions, used = [], Counter(), set()
    for p in prices:
        if p['league'] != 'K리그1':
            continue
        key = (p['day'], ALIASES[p['home']], ALIASES[p['away']])
        matches = index[key]
        if len(matches) != 1:
            exclusions['missing_or_ambiguous_' + p['day'][:4]] += 1
            continue
        g = matches[0]
        y = 0 if g['home_score'] > g['away_score'] else 2 if g['home_score'] < g['away_score'] else 1
        if y != p['y']:
            exclusions['outcome_conflict'] += 1
            continue
        if key in used:
            raise ValueError('Duplicate price fixture')
        used.add(key)
        output.append({'id': p['id'], 'source_game_id': g['gameId'], 'day': g['date'],
                       'home': g['home'], 'away': g['away'], 'y': y, 'odds': p['odds'],
                       'market_p': p['market_p'], 'home_score': g['home_score'],
                       'away_score': g['away_score'], 'home_xi': starting_xi(g, 'home'),
                       'away_xi': starting_xi(g, 'away')})
    return sorted(output, key=lambda r: (r['day'], r['id'])), exclusions


def projected(rows):
    """Only XI from completed past games under an explicit four-day availability lag."""
    pending, known, result = [], {}, []
    for seq, row in enumerate(sorted(rows, key=lambda r: (r['day'], r['id']))):
        today = date.fromisoformat(row['day'])
        while pending and pending[0][0] <= today:
            _, _, old = heapq.heappop(pending)
            for side in ['home', 'away']:
                previous = known.get(old[side])
                if previous is None or old['day'] >= previous['day']:
                    known[old[side]] = {'day': old['day'], 'xi': old[side+'_xi'], 'id': old['source_game_id']}
        r = dict(row)
        for side in ['home', 'away']:
            prior = known.get(row[side])
            r[side+'_projected'] = prior['xi'] if prior else []
            r[side+'_prior'] = prior
        result.append(r)
        heapq.heappush(pending, (today+timedelta(days=4), seq, row))
    return result


def terms(row, side, kind, oracle=False):
    other = 'away' if side == 'home' else 'home'
    values = {'intercept': 1., 'home_effect': float(side == 'home'),
              'attack_team:'+row[side]: 1., 'defence_team:'+row[other]: 1.}
    if kind == 'team':
        return values
    for role, which in [('attack', side), ('defence', other)]:
        ids = row[which+'_xi'] if oracle else row[which+'_projected']
        for pid in ids:
            values[role+'_player:'+pid] = 1/np.sqrt(11)
        if kind == 'pair':
            for a, b in itertools.combinations(sorted(ids), 2):
                values[role+'_pair:'+a+':'+b] = 1/np.sqrt(55)
    return values


def design(rows, kind, vocab=None, oracle=False):
    # Known historical actual XI are legitimate fitting inputs. Evaluation oracle is explicit.
    records = [terms(r, side, kind, oracle) for r in rows for side in ['home', 'away']]
    if vocab is None:
        vocab = {key: i for i, key in enumerate(sorted({key for rec in records for key in rec}))}
    rr, cc, vv = [], [], []
    for i, rec in enumerate(records):
        for key, value in rec.items():
            if key in vocab:
                rr.append(i); cc.append(vocab[key]); vv.append(value)
    return csr_matrix((vv, (rr, cc)), shape=(len(records), len(vocab))), vocab


def fit(rows, kind):
    x, vocab = design(rows, kind, oracle=True)
    y = np.array([r[s+'_score'] for r in rows for s in ['home', 'away']])
    penalty = np.array([.1 if '_pair:' in k else .03 if '_player:' in k else .01 for k in vocab])
    penalty[vocab['intercept']] = 0

    def loss_grad(beta):
        eta = x @ beta
        mu = np.exp(np.clip(eta, -10, 8))
        loss = np.mean(mu - y*eta) + .5*np.sum(penalty*beta*beta)
        grad = np.asarray(x.T @ (mu-y)).ravel()/len(y) + penalty*beta
        return loss, grad

    optimized = minimize(loss_grad, np.zeros(len(vocab)), jac=True, method='L-BFGS-B',
                         options={'maxiter': 500, 'ftol': 1e-11})
    if not optimized.success:
        raise RuntimeError(optimized.message)
    return optimized.x, vocab


def forecast(rows, kind, fitted, oracle=False):
    beta, vocab = fitted
    x, _ = design(rows, kind, vocab, oracle)
    means = np.exp(np.clip(x @ beta, -4, np.log(10))).reshape(-1, 2)
    probs = []
    for h, a in means:
        joint = np.outer(poisson.pmf(np.arange(41), h), poisson.pmf(np.arange(41), a))
        p = np.array([np.tril(joint, -1).sum(), np.trace(joint), np.triu(joint, 1).sum()])
        probs.append((p/p.sum()).tolist())
    return np.asarray(probs), means


def fit_market_offset(rows, kind):
    x, vocab = design(rows, kind, oracle=True)
    x = x[::2] - x[1::2]
    y = np.array([r['y'] for r in rows])
    market = np.array([r['market_p'] for r in rows])
    offset = np.log(market/market.sum(axis=1, keepdims=True))
    penalty = np.array([.1 if '_pair:' in k else .03 if '_player:' in k else .01 for k in vocab])
    target = np.eye(3)[y]

    def loss_grad(flat):
        beta = flat.reshape(len(vocab), 2)
        logits = offset.copy()
        logits[:, :2] += x @ beta
        p = softmax(logits, axis=1)
        loss = -np.log(np.maximum(p[np.arange(len(y)), y], 1e-12)).mean()
        loss += .5*np.sum(penalty[:, None]*beta*beta)
        grad = np.asarray(x.T @ (p-target)[:, :2])/len(y) + penalty[:, None]*beta
        return loss, grad.ravel()

    optimized = minimize(loss_grad, np.zeros(len(vocab)*2), jac=True, method='L-BFGS-B',
                         options={'maxiter':500, 'ftol':1e-11})
    if not optimized.success:
        raise RuntimeError(optimized.message)
    return optimized.x.reshape(len(vocab), 2), vocab


def forecast_market_offset(rows, kind, fitted, oracle=False):
    beta, vocab = fitted
    x, _ = design(rows, kind, vocab, oracle)
    x = x[::2]-x[1::2]
    market = np.array([r['market_p'] for r in rows])
    logits = np.log(market/market.sum(axis=1, keepdims=True))
    logits[:, :2] += x @ beta
    return softmax(logits, axis=1)


def metric(rows, p):
    y = np.array([r['y'] for r in rows]); picks = p.argmax(axis=1)
    nets = [Decimal(str(r['odds'][int(k)]))-1 if k == r['y'] else Decimal(-1) for r,k in zip(rows,picks)]
    curve = np.r_[0, np.array([float(v) for v in nets]).cumsum()]
    return {'n': len(rows), 'hits': int((picks==y).sum()), 'accuracy': float((picks==y).mean()),
            'roi': float(sum(nets)/len(rows)), 'logloss': float(-np.log(p[np.arange(len(rows)),y]).mean()),
            'brier': float(((p-np.eye(3)[y])**2).sum(axis=1).mean()),
            'mdd_units': float((np.maximum.accumulate(curve)-curve).max())}


def paired(rows, p, baseline):
    y=np.array([r['y'] for r in rows]);a=p.argmax(axis=1);b=baseline.argmax(axis=1)
    delta=np.array([[int(k==r['y'])-int(j==r['y']),
                     (r['odds'][k] if k==r['y'] else 0)-(r['odds'][j] if j==r['y'] else 0),
                     -np.log(max(p[i,r['y']],1e-12))+np.log(max(baseline[i,r['y']],1e-12))]
                    for i,(r,k,j) in enumerate(zip(rows,a,b))])
    keys=[date.fromisoformat(r['day']).isocalendar()[:2] for r in rows];weeks=sorted(set(keys))
    sums=np.array([delta[np.array([key==w for key in keys])].sum(axis=0) for w in weeks])
    counts=np.array([keys.count(w) for w in weeks]);rng=np.random.default_rng(20261108)
    draws=rng.integers(0,len(weeks),size=(2000,len(weeks)))
    samples=sums[draws].sum(axis=1)/counts[draws].sum(axis=1)[:,None]
    return {'changed_picks':int((a!=b).sum()),'delta_accuracy_roi_ll':delta.mean(axis=0).tolist(),
            'nominal_week_95_ci':np.quantile(samples,[.025,.975],axis=0).T.tolist(),'weeks':len(weeks)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lineups',required=True,type=Path)
    args=parser.parse_args();raw=json.loads(args.lineups.read_text(encoding='utf8'))
    price=ROOT/'experiments/all-sports-underdog-20261031/results/sc/domestic_archive/ledger.jsonl.gz'
    prices=[json.loads(line) for line in gzip.open(price,'rt',encoding='utf8')]
    connected,excluded=connect(raw,prices);connected=projected(connected)
    train=[r for r in connected if r['day']<='2025-12-27']
    test=[r for r in connected if '2026-01-01'<=r['day']<='2026-08-26' and r['home_projected'] and r['away_projected']]
    fits={kind:fit(train,kind) for kind in ['team','additive','pair']}
    probabilities={'market':np.array([r['market_p'] for r in test])};means={}
    for kind in ['team','additive','pair']:
        probabilities[kind+'_projected'],means[kind+'_projected']=forecast(test,kind,fits[kind])
        if kind!='team':probabilities[kind+'_oracle'],means[kind+'_oracle']=forecast(test,kind,fits[kind],oracle=True)
    for kind in ['team','additive','pair']:
        anchored = fit_market_offset(train, kind)
        probabilities['anchored_'+kind+'_projected'] = forecast_market_offset(test,kind,anchored)
        if kind!='team':probabilities['anchored_'+kind+'_oracle'] = forecast_market_offset(test,kind,anchored,oracle=True)
    for p in probabilities.values():assert np.isfinite(p).all() and np.allclose(p.sum(axis=1),1)
    summary={'train':['2023-01-01','2025-12-27'],'evaluation_requested':['2026-01-01','2026-08-26'],
             'evaluation_actual':[test[0]['day'],test[-1]['day']],'n_raw':len(raw),'n_connected':len(connected),
             'n_train':len(train),'n_test':len(test),'excluded':dict(excluded),
             'n_test_connected_before_projection_gate':sum('2026-01-01'<=r['day']<='2026-08-26' for r in connected),
             'design_iteration':'Market-offset ablation added after observing weak initial score-only predictions; exploratory, not preregistered.',
             'source_hashes':{'lineups':hashlib.sha256(args.lineups.read_bytes()).hexdigest(),
                              'prices':hashlib.sha256(price.read_bytes()).hexdigest()},
             'raw_source':'Derived Naver Sports lineup endpoint cache; HTTP receipt bodies/times unverified',
             'strict_pregame_capture_count':0,
             'limitations':['Actual-XI oracle predictions are retrospective; no historical pregame receipt proof.',
                            'Projected XI uses only archived match lineups assumed available day+4.',
                            'No possession/substitution timestamps: this is match-level shrinkage, NOT RAPM.',
                            'Prices have no proven HTTP receipt/executability; result direction only crosschecked.',
                            'No trained operational M0/tipster replay; repeated retrospective cohort.',
                            'Lineup and price matching uses unique date/home/away aliases, not identical source IDs.'],
             'models':{name:metric(test,p) for name,p in probabilities.items()},'paired':{},
             'feature_dimensions':{kind:len(fitted[1]) for kind,fitted in fits.items()}}
    for name,p in probabilities.items():
        if name!='market':summary['paired'][name+'_vs_market']=paired(test,p,probabilities['market'])
    for suffix in ['projected','oracle']:
        summary['paired']['pair_vs_additive_'+suffix]=paired(test,probabilities['pair_'+suffix],probabilities['additive_'+suffix])
        summary['paired']['anchored_pair_vs_additive_'+suffix]=paired(test,probabilities['anchored_pair_'+suffix],probabilities['anchored_additive_'+suffix])
    trained_players={pid for r in train for side in ['home','away'] for pid in r[side+'_xi']}
    pair_support=Counter(pair for r in train for side in ['home','away']
                         for pair in itertools.combinations(r[side+'_xi'],2))
    actual_players=[pid for r in test for side in ['home','away'] for pid in r[side+'_xi']]
    actual_pairs=[pair for r in test for side in ['home','away'] for pair in itertools.combinations(r[side+'_xi'],2)]
    summary['support']={'unique_training_players':len(trained_players),
                        'unique_training_pairs':len(pair_support),
                        'unseen_actual_player_share':sum(p not in trained_players for p in actual_players)/len(actual_players),
                        'unseen_actual_pair_share':sum(p not in pair_support for p in actual_pairs)/len(actual_pairs),
                        'actual_pair_training_count_median':float(np.median([pair_support[p] for p in actual_pairs])),
                        'mean_actual_vs_projected_changed_players':float(np.mean([len(set(r[side+'_xi'])-set(r[side+'_projected'])) for r in test for side in ['home','away']]))}
    with gzip.open(HERE/'connected.jsonl.gz','wt',encoding='utf8') as f:
        for r in connected:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    with gzip.open(HERE/'predictions.jsonl.gz','wt',encoding='utf8') as f:
        for name,p in probabilities.items():
            for r,q in zip(test,p):
                pick=int(np.argmax(q));net=Decimal(str(r['odds'][pick]))-1 if pick==r['y'] else Decimal(-1)
                record={'event_id':r['id'],'day':r['day'],'sport':'sc','league':'K리그1','product':'winner_3way_regulation',
                        'model':name,'p':q.tolist(),'y':r['y'],'odds':r['odds'],'pick':pick,'hit':pick==r['y'],
                        'net':str(net),'lineup_basis':('none' if name in ['market','team_projected','anchored_team_projected']
                                                     else 'oracle_actual_unproven' if name.endswith('oracle') else 'lagged_projection'),
                        'source_game_id':r['source_game_id']}
                f.write(json.dumps(record,ensure_ascii=False)+'\n')
    (HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(summary['models'],ensure_ascii=False))


if __name__=='__main__':main()
