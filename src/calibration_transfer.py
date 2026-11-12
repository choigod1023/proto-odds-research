"""Rolling, fixed-candidate calibration robustness study; no live promotion."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sport_hybrid_validation import load_data, fit, predict, score


def candidates(train, test):
    if train.date.max() >= test.date.min():
        raise ValueError('temporal overlap')
    pooled = predict(fit(train, [], True), test)
    recent = train[train.date >= test.date.min() - pd.Timedelta(days=365)]
    recent_p = predict(fit(recent, [], True), test) if len(recent) >= 200 else pooled.copy()
    league_p = pooled.copy()
    for league, idx in test.groupby('league').indices.items():
        local = train[train.league == league]
        if len(local) >= 200:
            league_p[idx] = .5 * pooled[idx] + .5 * predict(fit(local, [], True), test.iloc[idx])
    return dict(market=np.array(test.market.tolist()), pooled=pooled, recent=recent_p, league=league_p)


def run(path):
    data, quality = load_data(path)
    report = dict(quality=quality, independent_holdout=False, sports={})
    for sport in ('bk', 'sc', 'vl'):
        d = data[data.sport == sport]
        frames, predictions, folds = [], {}, []
        for period in sorted(d[d.year >= 2025].date.dt.to_period('Q').unique()):
            test = d[d.date.dt.to_period('Q') == period].reset_index(drop=True)
            train = d[d.date < period.start_time]
            if len(train) < 200 or len(test) < 20:
                continue
            ps = candidates(train, test)
            q = ps['market'].argmax(1) == test.y.to_numpy()
            folds.append(dict(period=str(period), n=len(test), accuracy={n:float((p.argmax(1)==test.y.to_numpy()).mean()) for n,p in ps.items()},
                              delta={n:float(((p.argmax(1)==test.y.to_numpy()).astype(float)-q).mean()) for n,p in ps.items()}))
            frames.append(test)
            for n,p in ps.items(): predictions.setdefault(n, []).append(p)
        all_test = pd.concat(frames, ignore_index=True)
        models = {}
        for name, parts in predictions.items():
            p = np.concatenate(parts)
            s = score(all_test, p)
            s['months'] = {}
            for month, idx in all_test.groupby(all_test.date.dt.to_period('M')).indices.items():
                q = np.array(all_test.iloc[idx].market.tolist())
                y = all_test.iloc[idx].y.to_numpy()
                s['months'][str(month)] = dict(n=len(idx), delta=float(((p[idx].argmax(1)==y).astype(float)-(q.argmax(1)==y)).mean()))
            models[name] = s
        report['sports'][sport] = dict(folds=folds, models=models)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--games', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.games)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({s:{n:{k:m[k] for k in ('accuracy','paired_accuracy_delta_ci','unit_top_pick_roi')} for n,m in v['models'].items()} for s,v in result['sports'].items()}))
