"""Offline exploratory comparison, NOT a leakage-safe promotion backtest.

Input facts were transcribed from public search/open responses, not inferred
from results. Full copyrighted articles are intentionally not redistributed.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import softmax
from sport_hybrid_validation import load_data, fit, predict
from model_v2 import SPORT_FEATURES

ALIASES = {
    'sc': {'서울': 'FC서울', '포항': '포항스틸', '서울이랜드': '서울이랜', '김포': '김포FC'},
    'bk': {'KT': 'KT소닉붐', '삼성': '서울삼성', 'SK': '서울SK', 'LG': '창원LG', '소노': '고양소노', 'KCC': '부산KCC'},
    'vl': {'현대캐피탈': '현대캐피'},
}


def augment(p, choices, strength=.25):
    """Fixed logit signal; strength is not fitted on the evaluation outcomes."""
    p = np.asarray(p, float)
    choices = np.asarray(choices, int)
    if p.ndim != 2 or len(choices) != len(p) or np.any(choices < 0) or np.any(choices >= p.shape[1]):
        raise ValueError('Invalid choices')
    z = np.log(np.clip(p, 1e-12, 1))
    z[np.arange(len(p)), choices] += strength
    return softmax(z, axis=1)


def metrics(y, odds, choice):
    y, odds, choice = np.asarray(y), np.asarray(odds), np.asarray(choice, int)
    if not len(y):
        return dict(n=0, accuracy=None, roi=None)
    hit = choice == y
    return dict(n=len(y), hits=int(hit.sum()), accuracy=float(hit.mean()),
                roi=float((odds[np.arange(len(y)), choice]*hit-1).mean()))


def match_records(picks, data):
    matched, audit = [], []
    for r in picks.to_dict('records'):
        a = dict(r)
        a['url'] = (f'https://www.attv24.com/sports_analysis/{r["id"]}' if r['source']=='attv24'
                    else f'https://www.goli.tv/board/analysis/{r["id"]}?page=1')
        a['strict_eligible'] = False  # No pregame snapshots in this backfill.
        a['timing_status'] = 'historical_backfill_no_pregame_snapshot'
        aliases = ALIASES.get(r['sport'], {})
        home, away = (aliases.get(r[k], r[k]) for k in ('home', 'away'))
        hits = data[data.date.eq(pd.Timestamp(r['date'])) & data.sport.eq(r['sport']) &
                    data.home_team.eq(home) & data.away_team.eq(away)]
        a['matching_events'] = len(hits)
        if r['conflict']:
            a['status'] = 'conflicting_card_and_body'
        elif len(hits) != 1:
            a['status'] = 'unmatched' if hits.empty else 'ambiguous_event'
        else:
            a['status'] = 'exploratory_only'
            idx = int(hits.index[0]); a['data_index'] = idx
            a['kickoff'] = str(hits.iloc[0].kickoff)
            a['choice_index'] = 0 if r['choice']=='home' else (2 if r['sport']=='sc' else 1)
            matched.append(a)
        audit.append(a)
    # Multiple pages must not give a physical event more weight.
    groups = {}
    for a in matched:
        groups.setdefault(a['data_index'], []).append(a)
    unique = []
    for group in groups.values():
        if len({a['choice_index'] for a in group}) != 1:
            for a in group: a['status'] = 'conflicting_duplicate_event'
        else:
            unique.append(group[0])
    return unique, audit


def run(games, picks_path):
    data, quality = load_data(games)
    picks = pd.read_csv(picks_path)
    joined, audit = match_records(picks, data)
    report = dict(scope='exploratory historical backfill; no promotion',
                  baseline='prior research market-residual model, NOT frozen production predictions',
                  games_sha256=hashlib.sha256(games.read_bytes()).hexdigest(),
                  picks_sha256=hashlib.sha256(picks_path.read_bytes()).hexdigest(),
                  collected_date_kst='2026-10-24', quality=quality, collected=len(picks),
                  strict_eligible=0, matched=len(joined), sports={}, audit=audit, ledger=[])
    for sport in ('bs','bk','sc','vl'):
        rows = [r for r in joined if r['sport']==sport]
        if not rows:
            report['sports'][sport] = dict(n=0, reason='no matched nonconflicting settled sample')
            continue
        d = data.loc[[r['data_index'] for r in rows]].copy()
        # All historical matched records are 2025; train only through 2024.
        cutoff = int(d.year.min())
        train = data[data.sport.eq(sport) & data.year.lt(cutoff)]
        assert train.date.max() < d.date.min()
        cols = list(dict.fromkeys(['elo_diff', *SPORT_FEATURES[sport]]))
        p = predict(fit(train, cols, True), d)
        tips = np.array([r['choice_index'] for r in rows])
        odds = np.array(d.odds.tolist()); y = d.y.to_numpy(int)
        policies = {'baseline':p.argmax(1), 'tipster_only':tips}
        for w in (.1,.25,.5):
            policies[f'logit_signal_{w}'] = augment(p,tips,w).argmax(1)
        scores = {name:metrics(y,odds,c) for name,c in policies.items()}
        for name, c in policies.items():
            scores[name]['changed_picks'] = int((c != policies['baseline']).sum())
            scores[name]['accuracy_delta'] = scores[name]['accuracy']-scores['baseline']['accuracy']
            scores[name]['roi_delta'] = scores[name]['roi']-scores['baseline']['roi']
        report['sports'][sport] = dict(n=len(d),train_n=len(train),train_end=str(train.date.max()),scores=scores)
        for j, (record, row) in enumerate(zip(rows,d.itertuples())):
            report['ledger'].append(dict(url=record['url'],sport=sport,date=record['date'],
                home=row.home_team,away=row.away_team,odds=odds[j].tolist(),y=int(y[j]),
                probability=p[j].tolist(),choices={k:int(v[j]) for k,v in policies.items()}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--games',type=Path,required=True)
    parser.add_argument('--picks',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = run(args.games,args.picks)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('collected','matched','strict_eligible','sports')},ensure_ascii=False,indent=2))
