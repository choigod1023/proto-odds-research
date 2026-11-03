"""Score the committed four-setting prediction blob after freeze validation."""
import argparse,collections,gzip,hashlib,importlib.util,json,math,subprocess,sys
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
SHA='669a0016f07275ed9136aa55fdf9f17c96f2c497a32d323a3cad9a2a0dcbab17';FREEZE='29b3ebe623b2260d28139ebe3ef422ed442cfacf'
LABEL='885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9';PRICE='24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'
NAMES=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter');ALL=NAMES+('frozen_team',)

def checked(path,sha):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('hash mismatch '+str(path))
    return raw

def load():
    raw=checked(P/'factorial/predictions.json',SHA)
    if subprocess.check_output(['git','show',FREEZE+':experiments/kbo-player-model-20261103/factorial/predictions.json'])!=raw:raise ValueError('predictions not equal to frozen Git blob')
    pred=json.loads(raw)
    if pred['eligible_pregame'] is not False or pred['historical_receipt_verified'] is not False:raise ValueError('invalid availability')
    # Outcomes and prices are read only after the prediction bytes and commit are verified.
    ys=[json.loads(z) for z in gzip.decompress(checked(P/'phase2/test-labels.jsonl.gz',LABEL)).splitlines()]
    if len(ys)!=726 or len({x['fixture_id'] for x in ys})!=726 or any(x['status']!='final' or any(type(x[k]) is not int or x[k]<0 for k in ('home_score','away_score')) for x in ys):raise ValueError('bad labels')
    prices=json.loads(checked(P/'phase3/overlay-results.json',PRICE))
    return pred,{y['fixture_id']:y for y in ys},prices

def threeway(x,name):return (x['frozen_team'] if name=='frozen_team' else x['settings'][name])['probabilities_3way']

def evaluate(rows,labels):
    records=[];acc={k:[] for k in ALL}
    for x in rows:
        y=labels[x['fixture_id']];truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';v={}
        for name in ALL:
            p=threeway(x,name);z=(int(max(('home','draw','away'),key=p.get)==truth),-math.log(max(p[truth],1e-15)),sum((p[k]-(k==truth))**2 for k in ('home','draw','away')));acc[name].append(z);v[name]=z
        records.append((x,v))
    n=len(rows);metrics={k:{'n':n,'hits':int(sum(z[0] for z in vs)),'accuracy_3way':sum(z[0] for z in vs)/n,'logloss_3way':sum(z[1] for z in vs)/n,'brier_3way':sum(z[2] for z in vs)/n} for k,vs in acc.items()};comparisons={}
    for name in NAMES[1:]:
        groups=collections.defaultdict(lambda:np.zeros(4));change3=change2=0;diff3=[];diff2=[]
        for x,v in records:
            aa=x['settings'][name];bb=x['settings']['original_projected'];a=aa['probabilities_3way'];b=bb['probabilities_3way'];change3+=max(('home','draw','away'),key=a.get)!=max(('home','draw','away'),key=b.get);p=aa['probabilities_2way'];q=bb['probabilities_2way'];change2+=max(('home','away'),key=p.get)!=max(('home','away'),key=q.get);diff3.extend(abs(a[k]-b[k]) for k in ('home','draw','away'));diff2.extend(abs(p[k]-q[k]) for k in ('home','away'));g=groups[date.fromisoformat(x['event_date']).isocalendar()[:2]];g+=np.array([1,*[a-b for a,b in zip(v[name],v['original_projected'])]])
        arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);draw=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1);ci={k:np.quantile(draw[:,i]/draw[:,0],[.025,.975]).tolist() for i,k in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1)}
        comparisons[name]={'changed_3way_picks':change3,'changed_2way_picks':change2,'mean_absolute_probability_change_3way_per_class':float(np.mean(diff3)),'mean_absolute_probability_change_2way_per_class':float(np.mean(diff2)),'weeks':len(groups),**ci}
    return {'n':n,'models':metrics,'versus_original':comparisons}

def price_results(rows,labels,prices,overlay):
    items={x['fixture_id']:x for x in rows};result={};ledgers={}
    for cohort,ls in prices['ledgers'].items():
        out=[]
        for old in ls:
            fid=old['fixture_id']
            if fid not in items:continue
            x=items[fid];y=labels[fid];winner='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
            if old['winner']!=winner:raise ValueError('phase3 outcome mismatch')
            forecasts=dict(old['forecasts'])
            for name in NAMES:
                p=x['settings'][name]['probabilities_2way'];vec=[p['home'],p['away']];pick=('home','away')[max(range(2),key=lambda i:vec[i])];void=winner=='draw';hit=not void and pick==winner;net=Decimal(0) if void else Decimal(str(old['price_source']['prices'][pick]))-1 if hit else Decimal(-1);forecasts[name]={'probabilities':vec,'pick':pick,'void':void,'hit':hit,'net':str(net)}
            for original,source in [('original_projected','player'),('team','team')]:
                pred=forecasts[original] if original!='team' else {'probabilities':[x['frozen_team']['probabilities_2way'][k] for k in ('home','away')]}
                if any(abs(a-b)>1e-12 for a,b in zip(pred['probabilities'],old['forecasts'][source]['probabilities'])):raise ValueError('phase3 probability mismatch')
            out.append({**old,'forecasts':forecasts})
        modes=NAMES+('team','market');result[cohort]={'n':len(out),'cohort_denominator':len(rows),'refunds':sum(x['winner']=='draw' for x in out),'source_counts':dict(collections.Counter(x['price_source']['origin'] for x in out)),'date_only_restored':sum(not x['price_source']['kickoff_matched'] for x in out),'models':{k:overlay.metrics(out,k) for k in modes},'versus_original':{k:overlay.paired(out,k,'original_projected') for k in NAMES[1:]},'versus_market':{k:overlay.paired(out,k,'market') for k in NAMES},'versus_team':{k:overlay.paired(out,k,'team') for k in NAMES}}
        ledgers[cohort]=out
    return result,ledgers

def main():
    p=argparse.ArgumentParser();p.add_argument('--prediction-sha256',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    if args.prediction_sha256!=SHA:raise ValueError('incorrect requested freeze')
    pred,labels,prices=load();spec=importlib.util.spec_from_file_location('_factorial_price_overlay',P/'phase3_overlay.py');overlay=importlib.util.module_from_spec(spec);sys.modules[spec.name]=overlay;spec.loader.exec_module(overlay)
    groups={'full_709':pred['predictions'],'original_193':[x for x in pred['predictions'] if x['cohort']=='original_193'],'new_516':[x for x in pred['predictions'] if x['cohort']=='new_complement']};results={};ledgers={}
    if [len(v) for v in groups.values()]!=[709,193,516]:raise ValueError('wrong cohort lengths')
    for name,rows in groups.items():
        three=evaluate(rows,labels);price,ledger=price_results(rows,labels,prices,overlay);results[name]={'threeway':three,'prices':price};ledgers[name]=ledger
    # Match H1 baseline and batting-only totals with the previously frozen result.
    previous=json.loads(checked(P/'phase4/results.json','71267f3ef9e8fea128dc7faec27530b1a9427f94691995a07d2a4688f40b0fc0'))
    for now,old in [('original_projected','original_player'),('official_batting_only','counterfactual_player')]:
        a=results['original_193']['threeway']['models'][now];b=previous['all_193']['models'][old]
        if a['hits']!=b['hits'] or any(abs(a[k]-b[k])>1e-12 for k in ('accuracy_3way','logloss_3way','brier_3way')):raise ValueError('H1 scoring mismatch')
        for pc in ('strict_original_time_match','extended_day_team_source_confirmed'):
            x=results['original_193']['prices'][pc]['models'][now];y=previous['price_cohorts'][pc]['models'][old]
            if x!=y:raise ValueError('H1 price settlement mismatch')
    output={'scenario':'retrospective_fixed_model_factorial_not_pit','prediction_sha256':SHA,'prediction_commit':FREEZE,'label_sha256':LABEL,'price_sha256':PRICE,'historical_receipt_verified':False,'actual_wallet_replay':False,'results':results,'price_ledgers':ledgers}
    with Path(args.output).open('x',encoding='utf8',newline='\n') as fp:json.dump(output,fp,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);fp.write('\n')
    print(json.dumps({k:{'n':v['threeway']['n'],'strict_prices':v['prices']['strict_original_time_match']['n'],'extended_prices':v['prices']['extended_day_team_source_confirmed']['n']} for k,v in results.items()}))
if __name__=='__main__':main()
