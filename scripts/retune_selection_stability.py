"""Validation-week winner stability for fixed candidates; never chooses on test outcomes."""
from collections import Counter,defaultdict
from datetime import date,timedelta
from pathlib import Path
import argparse,json,math

import numpy as np


def prepare(rows):
    if not rows:raise ValueError('empty_validation')
    names=None;weeks=defaultdict(dict)
    for row in rows:
        probabilities=dict(row.get('candidate_probabilities') or row.get('probabilities') or {})
        if 'market_null' not in probabilities:
            if 'market_probability' not in row:raise ValueError('missing_market_reference')
            probabilities['market_null']=row['market_probability']
        current=sorted(probabilities)
        if names is None:names=current
        if names!=current:raise ValueError('inconsistent_candidate_coverage')
        prices=row.get('prices') or row.get('historical_prices') or row.get('target',{}).get('prices')
        order=row.get('order') or row.get('target',{}).get('order') or ['home','away']
        if len(prices)!=2 or set(order)!={'home','away'} or any(not math.isfinite(v) or v<=1 for v in prices):raise ValueError('invalid_price_vector')
        hg=row.get('hg',row.get('home_score'));ag=row.get('ag',row.get('away_score'))
        if hg is None or ag is None:raise ValueError('missing_result')
        draw=hg==ag;homewin=hg>ag;d=date.fromisoformat(row['date']);week=(d-timedelta(days=d.weekday())).isoformat()
        for name,p in probabilities.items():
            if not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1:raise ValueError('invalid_probability')
            chosen='home' if p>=.5 else 'away';hit=int((chosen=='home')==homewin and not draw)
            profit=0. if draw else prices[order.index(chosen)]-1 if hit else -1.
            clipped=min(1-1e-9,max(1e-9,p))
            loss=0. if draw else -math.log(clipped if homewin else 1-clipped)
            brier=0. if draw else (p-int(homewin))**2
            if name not in weeks[week]:weeks[week][name]=np.zeros(6)
            weeks[week][name]+=[1,int(not draw),hit,profit,loss,brier]
    tensor=np.array([[weeks[w][n] for n in names] for w in sorted(weeks)])
    return names,sorted(weeks),tensor


def choose(names,sums):
    if np.any(sums[:,0]<=0) or np.any(sums[:,1]<=0):raise ValueError('no_settled_validation_bets')
    b=names.index('market_null');accuracy=sums[:,2]/sums[:,1];roi=sums[:,3]/sums[:,0]
    logloss=sums[:,4]/sums[:,1];brier=sums[:,5]/sums[:,1]
    eligible=[i for i in range(len(names)) if accuracy[i]>=accuracy[b]-1e-12 and roi[i]>=roi[b]-1e-12]
    i=min(eligible,key=lambda i:(logloss[i],brier[i],names[i]))
    return names[i]


def analyze(rows,repetitions=3000):
    names,weeks,tensor=prepare(rows);original=choose(names,tensor.sum(axis=0));counts=Counter();skipped=0
    rng=np.random.default_rng(20261029)
    for _ in range(repetitions):
        chosen=rng.integers(len(weeks),size=len(weeks));sums=tensor[chosen].sum(axis=0)
        try:counts[choose(names,sums)]+=1
        except ValueError:skipped+=1
    valid=sum(counts.values());shares={n:counts[n]/valid if valid else None for n in names}
    matrix=[]
    for row in rows:
        probabilities=dict(row.get('candidate_probabilities') or row.get('probabilities') or {})
        if 'market_null' not in probabilities:probabilities['market_null']=row['market_probability']
        matrix.append([probabilities[n] for n in names])
    matrix=np.array(matrix);original_p=matrix[:,names.index(original)]
    probability_shift=sum(shares[n]*float(np.mean(np.abs(matrix[:,i]-original_p))) for i,n in enumerate(names)) if valid else None
    decision_shift=sum(shares[n]*float(np.mean((matrix[:,i]>=.5)!=(original_p>=.5))) for i,n in enumerate(names)) if valid else None
    return dict(observed_validation_weeks=len(weeks),validation_rows=len(rows),candidate_count=len(names),repetitions=repetitions,
        skipped=skipped,original_primary=original,original_primary_share=shares[original],selection_shares=shares,
        weighted_mean_probability_shift_from_original=probability_shift,weighted_validation_pick_disagreement_from_original=decision_shift,
        interpretation='resampling the validation weeks of the SAME fixed fitted candidates; not refitting, not a future-profit probability, not selecting on evaluation')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repetitions',type=int,default=3000);a=p.parse_args()
    rows=[json.loads(s) for s in a.input.read_text(encoding='utf8').splitlines() if s]
    result=analyze(rows,a.repetitions);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(result,ensure_ascii=False))
