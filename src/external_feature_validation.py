"""Download public match statistics and test strictly lagged feature additions."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from accuracy_formula_lab import date_block_interval
from devig import market_probabilities
from features import build_features
from model_v2 import PI_PARAMS, PI_GAMMA, SPORT_FEATURES
import pi_ratings
from sport_hybrid_validation import KEY, fit, predict, brier, paper

DIVISIONS = {'E0': 'EPL', 'D1': '분데스리'}
STAT_PAIRS = {'shots': ('HS','AS'), 'on_target': ('HST','AST'),
              'corners': ('HC','AC'), 'red_cards': ('HR','AR')}
EXTERNAL = [f'{name}_diff' for name in STAT_PAIRS]
LEGACY = ['elo_diff', *SPORT_FEATURES['sc']]


def collect(cache):
    """Cache exact bytes and retrieval times; do not redistribute source files."""
    cache.mkdir(parents=True, exist_ok=True)
    url = ('https://raw.githubusercontent.com/xgabora/Club-Football-Match-Data/'
           '25882a58a736daf7ece3781940eac17ae1117a66/data/Matches.csv')
    path = cache / 'Matches.csv'
    meta = path.with_suffix('.meta.json')
    if not path.exists():
        raw = urlopen(Request(url, headers={'User-Agent':'sportstoto-offline-research/1.0'}),timeout=60).read()
        check = pd.read_csv(io.BytesIO(raw), low_memory=False)
        if not {'MatchDate','HomeTeam','AwayTeam','FTResult'}.issubset(check.columns):
            raise ValueError('Invalid mirror schema')
        path.write_bytes(raw)
        meta.write_text(json.dumps({'retrieved_at':datetime.now(timezone.utc).isoformat()}),encoding='utf8')
    raw = path.read_bytes()
    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    manifest = [dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),rows=len(df),
                     **json.loads(meta.read_text(encoding='utf8')))]
    df = df[df.Division.isin(DIVISIONS)].copy()
    stamp = pd.to_datetime(df.MatchDate)
    df['season'] = stamp.dt.year-(stamp.dt.month<7).astype(int)
    df.loc[stamp.between('2020-07-01','2020-08-31'),'season'] = 2019
    df = df[df.season.between(2019,2025)].copy()
    df['league'] = df.Division.map(DIVISIONS)
    mapping = {'MatchDate':'Date','FTHome':'FTHG','FTAway':'FTAG','FTResult':'FTR',
               'HomeShots':'HS','AwayShots':'AS','HomeTarget':'HST','AwayTarget':'AST',
               'HomeCorners':'HC','AwayCorners':'AC','HomeRed':'HR','AwayRed':'AR',
               'OddHome':'B365H','OddDraw':'B365D','OddAway':'B365A'}
    df = df.rename(columns=mapping)
    df['Date'] = pd.to_datetime(df.Date).dt.strftime('%d/%m/%Y')
    print(f'Collected mirror: {len(raw)} bytes; selected {len(df)} rows',flush=True)
    return df, manifest


def prepare(raw):
    d = raw.copy()
    d['date'] = pd.to_datetime(d.Date, dayfirst=True, errors='coerce').dt.normalize()
    d['home_team'], d['away_team'] = d.HomeTeam, d.AwayTeam
    d['sport'] = 'sc'
    d['kickoff'] = d.date  # Source date, not claimed UTC kickoff.
    d['year'] = d.date.dt.year
    d['home_score'] = pd.to_numeric(d.FTHG, errors='coerce')
    d['away_score'] = pd.to_numeric(d.FTAG, errors='coerce')
    d['y'] = d.FTR.map({'H':0, 'D':1, 'A':2})
    quality = {'source_rows':len(d)}
    d = d.dropna(subset=['date','league','home_team','away_team','home_score','away_score','y'])
    expected = np.where(d.home_score > d.away_score, 0, np.where(d.home_score == d.away_score,1,2))
    valid = d.y.eq(expected) & d.home_score.ge(0) & d.away_score.ge(0)
    quality['score_conflicts'] = int((~valid).sum())
    d = d[valid]
    duplicate = d.duplicated(KEY, keep=False)
    quality['duplicate_rows_excluded'] = int(duplicate.sum())
    d = d[~duplicate].sort_values(KEY).reset_index(drop=True)
    d['outcome'] = d.y.map({0:1.,1:.5,2:0.})
    for pair in STAT_PAIRS.values():
        for col in pair:
            d[col] = pd.to_numeric(d[col], errors='coerce')
            d.loc[d[col]<0,col] = np.nan
    return d, quality


def lagged_stats(d):
    """Only games dated at least two days earlier enter a team's last-ten history."""
    history = defaultdict(lambda:deque(maxlen=10))
    queue = deque()
    rows = []
    for row in d.sort_values(KEY).to_dict('records'):
        cutoff = row['date'] - pd.Timedelta(days=2)
        while queue and queue[0]['date'] <= cutoff:
            old = queue.popleft()
            for side, teamcol in ((0,'home_team'),(1,'away_team')):
                history[(old['league'],old[teamcol])].append(
                    [old[pair[side]]-old[pair[1-side]] for pair in STAT_PAIRS.values()])
        h = history[(row['league'],row['home_team'])]
        a = history[(row['league'],row['away_team'])]
        item = {k:row[k] for k in KEY}
        for i, name in enumerate(EXTERNAL):
            hv = [v[i] for v in h if np.isfinite(v[i])]
            av = [v[i] for v in a if np.isfinite(v[i])]
            item[name] = float(np.mean(hv)-np.mean(av)) if len(hv)>=3 and len(av)>=3 else np.nan
        rows.append(item)
        queue.append(row)
    return pd.DataFrame(rows)


def attach_features(d):
    old = pi_ratings.LAMBDA, pi_ratings.DAMP, pi_ratings.GAMMA
    try:
        pi_ratings.LAMBDA = {s:p[0] for s,p in PI_PARAMS.items()}
        pi_ratings.DAMP = {s:p[1] for s,p in PI_PARAMS.items()}
        pi_ratings.GAMMA = PI_GAMMA
        pi = pi_ratings.run_pi(d)
    finally:
        pi_ratings.LAMBDA, pi_ratings.DAMP, pi_ratings.GAMMA = old
    f = build_features(d)
    f = f.merge(pi[KEY+['pi_diff']], on=KEY, validate='one_to_one')
    f = f.merge(lagged_stats(d), on=KEY, validate='one_to_one')
    f = f.merge(d[KEY+['season','y','B365H','B365D','B365A']], on=KEY, validate='one_to_one')
    prices = f[['B365H','B365D','B365A']].apply(pd.to_numeric, errors='coerce')
    valid = np.isfinite(prices).all(axis=1) & (prices>1).all(axis=1)
    f = f[valid].copy()
    f['odds'] = prices[valid].values.tolist()
    f['market'] = f.odds.map(market_probabilities)
    return f.reset_index(drop=True)


def evaluate(d, p, baseline):
    y = d.y.to_numpy(int)
    hit = (p.argmax(1)==y).astype(float)
    base_hit = (baseline.argmax(1)==y).astype(float)
    delta = pd.DataFrame({'date':d.date, 'accuracy':hit-base_hit,
                          'brier':brier(p,y)-brier(baseline,y)})
    pnl = np.array(d.odds.tolist())[np.arange(len(d)), p.argmax(1)]*hit-1
    def brief(idx):
        return dict(n=len(idx), accuracy=float(hit[idx].mean()),
                    accuracy_delta=float((hit-base_hit)[idx].mean()), roi=float(pnl[idx].mean()))
    return dict(n=len(d), accuracy=float(hit.mean()), brier=float(brier(p,y).mean()),
                log_loss=float(-np.log(np.clip(p[np.arange(len(d)),y],1e-12,1)).mean()),
                roi=float(pnl.mean()), versus_legacy_ci=date_block_interval(delta,['accuracy','brier'],repeats=2000,seed=20261024,block_dates=7),
                leagues={str(k):brief(v) for k,v in d.groupby('league').indices.items()},
                seasons={str(k):brief(v) for k,v in d.groupby('season').indices.items()},
                paper={str(w):paper(d,p,w) for w in (2,3)})


def run(cache):
    raw, manifest = collect(cache)
    matches, quality = prepare(raw)
    d = attach_features(matches)
    quality.update(valid_result_rows=len(matches), usable_price_rows=len(d),
                   missing_external_rows=int(d[EXTERNAL].isna().any(axis=1).sum()))
    outputs, predictions, folds = [], defaultdict(list), []
    for season in (2023,2024,2025):
        train, test = d[d.season<season], d[d.season==season]
        if train.empty or test.empty or train.date.max()>=test.date.min():
            raise ValueError('Empty or temporally overlapping fold')
        ps = {'market':np.array(test.market.tolist())}
        for name, cols in (('legacy',LEGACY),('external',EXTERNAL),('combined',LEGACY+EXTERNAL)):
            ps[name] = predict(fit(train,cols,True),test)
        ps['blend75'] = .75*ps['market'] + .25*ps['combined']
        outputs.append(test)
        for name,p in ps.items(): predictions[name].append(p)
        folds.append(dict(season=season,train=len(train),test=len(test),start=str(test.date.min().date()),end=str(test.date.max().date())))
    out = pd.concat(outputs,ignore_index=True)
    ps = {name:np.concatenate(parts) for name,parts in predictions.items()}
    return dict(source='Football-Data.co.uk', manifest=manifest, quality=quality, folds=folds,
                scope='external soccer benchmark; reconstructed legacy architecture, not live saved picks',
                models={name:evaluate(out,p,ps['legacy']) for name,p in ps.items()})


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.cache)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({n:{k:r[k] for k in ('n','accuracy','brier','roi','versus_legacy_ci')} for n,r in result['models'].items()}))
