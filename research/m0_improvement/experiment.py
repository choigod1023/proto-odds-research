"""Finite M0 ablations with a development-only selection and frozen replay.

This is retrospective: archived actual lineups are not proven pregame receipts.
The 2026 cohort has already been examined; it is not a new blind holdout.
"""
import argparse
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix
from scipy.stats import skellam

VARIANTS = ('baseline', 'rest_cap', 'small_sample_shrinkage', 'combined')
FEATURES = tuple(s+'_'+k for s in ('home','away') for k in
                 ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load'))+('park_factor',)
PROTOCOL = Path(__file__).with_name('protocol.json')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as out:
        json.dump(obj, out, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        out.write('\n')


def load_rows(path):
    raw = Path(path).read_bytes()
    rows = [json.loads(x) for x in gzip.decompress(raw).splitlines() if x]
    if len({x['fixture_id'] for x in rows}) != len(rows):
        raise ValueError('duplicate fixture')
    return rows, digest(raw)


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('nonnegative integer history component required')
    return value


def histories(entries, row):
    seen = set()
    for h in entries or []:
        if h['fixture_id'] == row['fixture_id'] or h['fixture_id'] in seen or date.fromisoformat(h['date']) >= date.fromisoformat(row['event_date']):
            raise ValueError('target/future/duplicate player history')
        seen.add(h['fixture_id'])
        yield h


def transform(row, variant):
    if variant not in VARIANTS:
        raise ValueError('unregistered candidate')
    if (type(row['season']) is not int or date.fromisoformat(row['event_date']).year != row['season']
            or row['home_id'] == row['away_id'] or set(row['features']) != set(FEATURES)):
        raise ValueError('invalid fixture identity/features')
    out = deepcopy(row)
    for v in out['features'].values():
        if v['value'] is not None and (type(v['value']) not in (int,float) or not math.isfinite(v['value'])):
            raise ValueError('invalid scalar')
    for side in ('home','away'):
        if variant in ('rest_cap','combined'):
            key = side+'_rest_days'; v = out['features'][key]['value']
            if v is not None:
                if v < 0: raise ValueError('negative rest')
                out['features'][key]['value'] = min(v, 7.)
        if variant not in ('small_sample_shrinkage','combined'):
            continue
        # The pseudo-counts below are fixed before evaluation, not ROI-tuned.
        key = side+'_lineup_strength'; roster = row['roster'][side]
        people = row['raw_features'][key]['value']
        if people is not None:
            if len(people) != 9 or [x['player_id'] for x in people] != roster['hitters'] or len(set(roster['hitters'])) != 9:
                raise ValueError('history does not belong to ordered lineup')
            rates = []
            for person in people:
                ab = hit = bb = 0
                for h in histories(person['history'],row):
                    a,b,c = (count(h[k]) for k in ('ab','hit','bb'))
                    if b > a: raise ValueError('hits exceed at bats')
                    ab += a; hit += b; bb += c
                # Neutral prior for no history is explicit rather than observed 0.
                rates.append((hit+bb+100*.33)/(ab+bb+100))
            out['features'][key]['value'] = sum(rates)/9
        key = side+'_starter_strength'; value = row['raw_features'][key]['value']
        if value is not None:
            if value['projected_player_id'] != roster['starter']:
                raise ValueError('starter history owner differs')
            er = outs = 0
            for h in histories(value['appearances'],row):
                er += count(h['er']); outs += count(h['outs'])
            out['features'][key]['value'] = 27*(er+4.5*54/27)/(outs+54)
    return out


def scalar_rows(rows):
    output = []
    for r in rows:
        f = r['features']
        for s,o in (('home','away'),('away','home')):
            names = [s+'_team_strength',o+'_team_strength',s+'_rest_days',o+'_rest_days',
                     'park_factor',s+'_lineup_strength',o+'_starter_strength',o+'_bullpen_load']
            output.append([np.nan if f[n]['value'] is None else f[n]['value'] for n in names])
    return np.array(output, dtype=float)


def make_spec(rows):
    nums = scalar_rows(rows); mask = np.isfinite(nums); n = mask.sum(0)
    mean = np.divide(np.where(mask,nums,0).sum(0),n,out=np.zeros(8),where=n>0)
    variance = np.divide(np.where(mask,(nums-mean)**2,0).sum(0),n,out=np.zeros(8),where=n>0)
    scale = np.sqrt(variance); scale[scale < 1e-12] = 1.
    return {'teams':sorted({r[k] for r in rows for k in ('home_id','away_id')}),
            'mean':mean.tolist(),'scale':scale.tolist()}


def design(rows, spec):
    nums = scalar_rows(rows); missing = ~np.isfinite(nums)
    norm = (np.where(missing,spec['mean'],nums)-spec['mean'])/spec['scale']
    output = []; teams = spec['teams']
    for i,r in enumerate(rows):
        if r['home_id'] not in teams or r['away_id'] not in teams:
            raise ValueError('unseen team identity')
        for j,(s,o) in enumerate((('home','away'),('away','home'))):
            ix = i*2+j
            output.append([1.,float(s=='home')]+[float(r[s+'_id']==t) for t in teams]
                          +[float(r[o+'_id']==t) for t in teams]+norm[ix].tolist()+missing[ix].astype(float).tolist())
    return csr_matrix(np.array(output))


def fit(rows, labels, variant, first_target):
    if not rows or any(date.fromisoformat(r['event_date']) > date.fromisoformat(first_target)-timedelta(days=7) for r in rows):
        raise ValueError('training must precede target by seven calendar days')
    transformed = [transform(r,variant) for r in rows]
    spec = make_spec(transformed); x = design(transformed,spec)
    y = []
    for r in rows:
        z = labels[r['fixture_id']]
        if z['status'] != 'final': raise ValueError('nonfinal training target')
        y.extend(count(z[k]) for k in ('home_score','away_score'))
    y = np.array(y,dtype=float); penalty = np.r_[0.,np.full(x.shape[1]-1,10.)]
    def objective(beta):
        eta = x@beta
        if np.max(np.abs(eta)) > 30: return 1e100,np.zeros_like(beta)
        mu = np.exp(eta)
        return float(np.sum(mu-y*eta)+.5*np.sum(beta*beta*penalty)),np.asarray(x.T@(mu-y))+penalty*beta
    initial = np.zeros(x.shape[1]); initial[0] = math.log(max(float(y.mean()),.1))
    result = minimize(objective,initial,jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('optimizer did not converge')
    return {'variant':variant,'spec':spec,'beta':result.x.tolist(),'training_n':len(rows),
            'last_training_date':max(r['event_date'] for r in rows),'iterations':int(result.nit),
            'objective':float(result.fun),'promoted':False}


def predict(model, rows):
    x = design([transform(r,model['variant']) for r in rows],model['spec']); eta = x@np.array(model['beta'])
    if not np.isfinite(eta).all() or np.max(np.abs(eta)) > 20: raise ValueError('unstable rates')
    rates = np.exp(eta).reshape(-1,2); h,a = rates.T
    prob = np.column_stack((skellam.sf(0,h,a),skellam.pmf(0,h,a),skellam.cdf(-1,h,a)))
    if not np.isfinite(prob).all() or np.any(prob < 0): raise ValueError('invalid outcome mass')
    prob /= prob.sum(axis=1)[:,None]
    return [{'fixture_id':r['fixture_id'],'event_date':r['event_date'],
             'probabilities_3way':dict(zip(('home','draw','away'),map(float,prob[i]))),
             'home_probability':float(prob[i,0]/(prob[i,0]+prob[i,2])),
             'home_rate':float(h[i]),'away_rate':float(a[i])} for i,r in enumerate(rows)]


def metrics(predictions, labels, prices=None):
    n = hits = decided = bets = refunds = 0; ll = brier = 0.; net = Decimal(0)
    for p in predictions:
        fid = p['fixture_id']; z = labels[fid]; h,a = count(z['home_score']),count(z['away_score'])
        if z['status'] != 'final': raise ValueError('nonfinal evaluation result')
        winner = 'home' if h>a else 'away' if a>h else 'draw'; n+=1
        ll -= math.log(max(p['probabilities_3way'][winner],1e-15))
        brier += sum((p['probabilities_3way'][k]-int(k==winner))**2 for k in ('home','draw','away'))
        q = p['home_probability']; pick = None if abs(q-.5)<=1e-12 else 'home' if q>.5 else 'away'
        if winner!='draw':
            decided+=1; hits+=int(pick==winner)
        if prices is not None and fid in prices and pick is not None:
            odds = Decimal(str(prices[fid][pick]))
            if not odds.is_finite() or odds<=1: raise ValueError('invalid stored price')
            bets+=1; refunds+=int(winner=='draw')
            net += Decimal(0) if winner=='draw' else odds-1 if pick==winner else Decimal(-1)
    return {'fixtures':n,'decided':decided,'hits':hits,'accuracy':hits/decided if decided else None,
            'log_loss_3way':ll/n if n else None,'brier_3way':brier/n if n else None,
            'bets':bets,'refunds':refunds,'net_units':str(net),'roi':float(net/bets) if bets else None}


def select_candidate(scores):
    base = scores['baseline']
    eligible = [v for v in VARIANTS if scores[v]['accuracy']>=base['accuracy'] and scores[v]['log_loss_3way']<=base['log_loss_3way']]
    return min(eligible,key=lambda v:(scores[v]['log_loss_3way'],VARIANTS.index(v)))


def committed(path, revision):
    path = Path(path).resolve(); root = Path(__file__).resolve().parents[2]
    relative = path.relative_to(root).as_posix(); raw = path.read_bytes()
    if len(revision)!=40 or subprocess.check_output(['git','-C',str(root),'show',revision+':'+relative]) != raw:
        raise ValueError('artifact must match explicit frozen Git revision')
    return json.loads(raw)


def develop(source, output):
    rows,rs = load_rows(source/'inputs/paired-train-actual.jsonl.gz')
    labs,ls = load_rows(source/'inputs/development-labels.jsonl.gz'); labels={r['fixture_id']:r for r in labs}
    if any(r['season'] not in (2023,2024,2025) for r in rows): raise ValueError('development includes test season')
    train=[r for r in rows if r['season']<2025]; val=[r for r in rows if r['season']==2025]
    scores = {}; models = {}
    for v in VARIANTS:
        model=fit(train,labels,v,min(r['event_date'] for r in val)); models[v]=model
        scores[v]=metrics(predict(model,val),labels)
    result={'stage':'development','protocol_sha256':digest(PROTOCOL.read_bytes()),'source_sha256':{'training':rs,'labels':ls},
            'training_n':len(train),'validation_n':len(val),'scores':scores,'selected':select_candidate(scores),
            'models':models,'test_labels_read':False,'historical_receipt_proven':False,'promoted':False}
    dump(output/'development.json',result)
    print(json.dumps({k:result[k] for k in ('training_n','validation_n','scores','selected')}))


def freeze(source, output, revision):
    dev=committed(output/'development.json',revision)
    if dev['protocol_sha256']!=digest(PROTOCOL.read_bytes()) or dev['test_labels_read'] or dev['selected']!=select_candidate(dev['scores']):
        raise ValueError('development freeze differs')
    rows,rs=load_rows(source/'inputs/paired-train-actual.jsonl.gz'); target,ts=load_rows(source/'inputs/paired-test-actual.jsonl.gz')
    labs,ls=load_rows(source/'inputs/development-labels.jsonl.gz'); labels={r['fixture_id']:r for r in labs}
    if digest((source/'inputs/paired-train-actual.jsonl.gz').read_bytes())!=dev['source_sha256']['training'] or ls!=dev['source_sha256']['labels']:
        raise ValueError('training bytes changed after selection')
    if any(r['season']!=2026 for r in target) or any(r['season'] not in (2023,2024,2025) for r in rows):
        raise ValueError('final split differs')
    models={v:fit(rows,labels,v,min(r['event_date'] for r in target)) for v in VARIANTS}
    dump(output/'frozen-predictions.json',{'development_commit':revision,'protocol_sha256':dev['protocol_sha256'],
         'selected':dev['selected'],'training_n':len(rows),'target_n':len(target),'target_sha256':ts,
         'models':models,'predictions':{v:predict(m,target) for v,m in models.items()},
         'test_labels_read':False,'promoted':False})
    print(json.dumps({'training_n':len(rows),'target_n':len(target),'selected':dev['selected']}))


def evaluate(source, output, revision, replay):
    frozen=committed(output/'frozen-predictions.json',revision)
    if frozen['test_labels_read'] or frozen['protocol_sha256']!=digest(PROTOCOL.read_bytes()): raise ValueError('prediction freeze differs')
    labs,ls=load_rows(source/'evaluation-inputs/test-labels.jsonl.gz'); labels={r['fixture_id']:r for r in labs}
    replay_raw=Path(replay).read_bytes(); previous=json.loads(replay_raw)
    prices={r['fixture_id']:r['prices'] for r in previous['rows']}
    for r in previous['rows']:
        z=labels[r['fixture_id']]; winner='home' if z['home_score']>z['away_score'] else 'away' if z['away_score']>z['home_score'] else 'draw'
        if winner!=r['winner']: raise ValueError('result sources disagree')
    pred=frozen['predictions']; base_idx={p['fixture_id']:p for p in pred['baseline']}
    if len(prices)!=667 or any(fid not in base_idx for fid in prices): raise ValueError('price cohort changed')
    # Independently refitted baseline must reproduce the prior same-cohort result.
    max_error=max(abs(base_idx[r['fixture_id']]['home_probability']-r['models']['m0_actual']['home_probability']) for r in previous['rows'])
    if max_error>2e-4: raise ValueError('M0 control reproduction differs: '+str(max_error))
    priced={v:[p for p in ps if p['fixture_id'] in prices] for v,ps in pred.items()}
    cohorts={'all667':priced}
    for year_month in sorted({p['event_date'][:7] for p in priced['baseline']}):
        cohorts[year_month]={v:[p for p in ps if p['event_date'].startswith(year_month)] for v,ps in priced.items()}
    result={'stage':'retrospective_replay','frozen_prediction_commit':revision,'selected_before_2026_evaluation':frozen['selected'],
            'protocol_sha256':frozen['protocol_sha256'],'test_labels_sha256':ls,'price_replay_sha256':digest(replay_raw),
            'max_baseline_probability_error':max_error,'historical_receipt_proven':False,'independent_blind_holdout':False,
            'metrics':{group:{v:metrics(ps,labels,prices) for v,ps in sets.items()} for group,sets in cohorts.items()},
            'all709_accuracy_diagnostics':{v:metrics(ps,labels) for v,ps in pred.items()},
            'production_changed':False,'paper_wallet_changed':False,'promoted':False,
            'evaluation_scope':'2026 KBO already-examined actual-roster archive; not live operating vote performance'}
    dump(output/'results.json',result)
    print(json.dumps({k:result[k] for k in ('selected_before_2026_evaluation','max_baseline_probability_error','metrics')}))


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('stage',choices=('develop','freeze','evaluate'))
    p.add_argument('--source',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--revision'); p.add_argument('--replay',type=Path); a=p.parse_args()
    if a.stage=='develop': develop(a.source,a.output)
    elif a.stage=='freeze': freeze(a.source,a.output,a.revision)
    else: evaluate(a.source,a.output,a.revision,a.replay)


if __name__=='__main__': main()
