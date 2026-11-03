"""Score frozen official-batting counterfactual; never refits or mutates old data."""
import argparse,collections,gzip,hashlib,importlib.util,json,math,subprocess,sys
from datetime import date
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
P4SHA='5c0a9d88287e0bf84747003386cecac0a81684b169ec08604776de3669679c17'
P2SHA='d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'
LABELSHA='885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9'
PRICE3SHA='24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'
FREEZE='516d5068'

def read(path,sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('frozen input hash mismatch')
    return raw

def frozen_inputs():
    raw=read(P/'phase4/predictions.json',P4SHA)
    blob=subprocess.check_output(['git','show',FREEZE+':experiments/kbo-player-model-20261103/phase4/predictions.json'])
    if blob!=raw:raise ValueError('frozen prediction is not the committed Git blob')
    pred=json.loads(raw)
    if pred['scenario']!='retrospective_official_batting_order_assumed_available' or any(pred[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('unsafe scenario')
    baseline=json.loads(read(P/'phase2/predictions.json',P2SHA))
    # Outcome-bearing files are read only after frozen prediction hash/commit validation.
    labels=[json.loads(x) for x in gzip.decompress(read(P/'phase2/test-labels.jsonl.gz',LABELSHA)).splitlines()]
    if len(labels)!=726 or len({x['fixture_id'] for x in labels})!=726 or any(x['status']!='final' or any(type(x[k]) is not int or x[k]<0 for k in ('home_score','away_score')) for x in labels):raise ValueError('invalid or duplicated outcome labels')
    prices=json.loads(read(P/'phase3/overlay-results.json',PRICE3SHA))
    return pred,baseline,labels,prices

def threeway(entry,key):return [entry[key]['probabilities_3way'][k] for k in ('home','draw','away')]

def all193(pred,labels):
    outcomes={v['fixture_id']:v for v in labels};items=pred['predictions'];n=len(items)
    if n!=193 or len({v['fixture_id'] for v in items})!=n or not {v['fixture_id'] for v in items}<=set(outcomes):raise ValueError('lineup cohort mismatch')
    accum={k:{'hits':0,'ll':0.,'brier':0.} for k in ('original_player','counterfactual_player','frozen_team')}
    records=[];change3=0;change2=0;abs3=[];abs2=[]
    for x in items:
        y=outcomes[x['fixture_id']];a=y['home_score'];b=y['away_score'];truth=0 if a>b else 1 if a==b else 2
        rec={'fixture_id':x['fixture_id'],'date':x['event_date'],'truth':truth,'metrics':{}}
        for k in accum:
            vec=threeway(x,k);hit=int(max(range(3),key=lambda i:vec[i])==truth);loss=-math.log(max(vec[truth],1e-15));brier=sum((v-int(i==truth))**2 for i,v in enumerate(vec));accum[k]['hits']+=hit;accum[k]['ll']+=loss;accum[k]['brier']+=brier;rec['metrics'][k]={'hit':hit,'logloss':loss,'brier':brier}
        pa=threeway(x,'original_player');pb=threeway(x,'counterfactual_player');p2a=x['original_player']['probabilities_2way'];p2b=x['counterfactual_player']['probabilities_2way'];change3+=max(range(3),key=lambda i:pa[i])!=max(range(3),key=lambda i:pb[i]);change2+=max(('home','away'),key=lambda k:p2a[k])!=max(('home','away'),key=lambda k:p2b[k]);abs3.extend(abs(a-b) for a,b in zip(pa,pb));abs2.extend(abs(p2a[k]-p2b[k]) for k in ('home','away'));records.append(rec)
    metrics={k:{'n':n,'hits':v['hits'],'accuracy_3way':v['hits']/n,'logloss_3way':v['ll']/n,'brier_3way':v['brier']/n} for k,v in accum.items()}
    groups=collections.defaultdict(lambda:np.zeros(4))
    for rec in records:
        a=rec['metrics']['counterfactual_player'];b=rec['metrics']['original_player'];g=groups[date.fromisoformat(rec['date']).isocalendar()[:2]];g+=np.array([1,a['hit']-b['hit'],a['logloss']-b['logloss'],a['brier']-b['brier']])
    bootstrap={'weeks':len(groups),'accuracy_delta_ci95':None,'logloss_delta_ci95':None,'brier_delta_ci95':None}
    if len(groups)>=2:
        arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);draw=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
        for pos,key in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1):bootstrap[key]=np.quantile(draw[:,pos]/draw[:,0],[.025,.975]).tolist()
    return {'n':n,'models':metrics,'counterfactual_vs_original':{'changed_3way_picks':change3,'changed_2way_picks':change2,'mean_absolute_probability_change_3way_per_class':float(np.mean(abs3)),'mean_absolute_probability_change_2way_per_class':float(np.mean(abs2)),**bootstrap}},records


def price_cohorts(pred,labels,prices,overlay):
    ys={v['fixture_id']:v for v in labels};items={v['fixture_id']:v for v in pred['predictions']};out={};ledgers={}
    for cohort,allrows in prices['ledgers'].items():
        entries=[]
        for old in allrows:
            fid=old['fixture_id']
            if fid not in items:continue
            x=items[fid];winner='home' if ys[fid]['home_score']>ys[fid]['away_score'] else 'away' if ys[fid]['home_score']<ys[fid]['away_score'] else 'draw'
            if old['winner']!=winner:raise ValueError('phase3 label mismatch')
            oldp=x['original_player']['probabilities_2way'];team=x['frozen_team']['probabilities_2way']
            for key,p in [('player',oldp),('team',team)]:
                if any(abs(old['forecasts'][key]['probabilities'][i]-p[side])>1e-12 for i,side in enumerate(('home','away'))):raise ValueError('phase3 original probability mismatch')
            forecast=dict(old['forecasts']);forecast['original_player']=forecast['player'];p=x['counterfactual_player']['probabilities_2way'];vec=[p['home'],p['away']];pick=('home','away')[max(range(2),key=lambda i:vec[i])];void=winner=='draw';hit=pick==winner and not void
            from decimal import Decimal
            net=Decimal(0) if void else Decimal(str(old['price_source']['prices'][pick]))-1 if hit else Decimal(-1)
            forecast['counterfactual_player']={'probabilities':vec,'pick':pick,'hit':hit,'void':void,'net':str(net)}
            entries.append({**old,'forecasts':forecast})
        modes=('market','team','original_player','counterfactual_player');n=len(entries);diff=[]
        for e in entries:diff.extend(abs(a-b) for a,b in zip(e['forecasts']['counterfactual_player']['probabilities'],e['forecasts']['original_player']['probabilities']))
        out[cohort]={'n':n,'lineup_cohort_denominator':193,'season_denominator':726,'excluded_from_lineup_cohort':193-n,'refunds':sum(v['winner']=='draw' for v in entries),'source_counts':dict(collections.Counter(e['price_source']['origin'] for e in entries)),'date_only_restored':sum(not e['price_source']['kickoff_matched'] for e in entries),'date_range':[min((e['date'] for e in entries),default=None),max((e['date'] for e in entries),default=None)],'models':{k:overlay.metrics(entries,k) for k in modes},'counterfactual_vs_original':{**overlay.paired(entries,'counterfactual_player','original_player'),'mean_absolute_probability_change_2way_per_class':float(np.mean(diff)) if diff else None},'counterfactual_vs_market':overlay.paired(entries,'counterfactual_player','market'),'counterfactual_vs_team':overlay.paired(entries,'counterfactual_player','team')}
        ledgers[cohort]=entries
    return out,ledgers

def main():
    p=argparse.ArgumentParser();p.add_argument('--predictions-sha256',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    if a.predictions_sha256!=P4SHA:raise ValueError('unexpected prediction freeze SHA')
    pred,baseline,labels,prices=frozen_inputs()
    expected={x['fixture_id']:x for x in baseline['predictions']['player']}
    for x in pred['predictions']:
        if x['original_player']!=expected.get(x['fixture_id']):raise ValueError('original predictions changed')
    spec=importlib.util.spec_from_file_location('_phase4_overlay_metrics',P/'phase3_overlay.py');overlay=importlib.util.module_from_spec(spec);sys.modules[spec.name]=overlay;spec.loader.exec_module(overlay)
    total,unpriced=all193(pred,labels);cohorts,ledgers=price_cohorts(pred,labels,prices,overlay)
    output={'scenario':pred['scenario'],'strict_pit_eligible':False,'historical_receipt_verified':False,'actual_pitcher_source':'previous-game projection','season_denominator':726,'official_lineup_cohort':193,'unprocessed_not_missing':533,'missing_history_players':pred['missing_history_players'],'source_price_execution_verified':False,'actual_wallet_replay':False,'all_193':total,'price_cohorts':cohorts,'unpriced_score_rows':unpriced,'price_ledgers':ledgers,'input_sha256':{'new_predictions':P4SHA,'original_predictions':P2SHA,'test_labels':LABELSHA,'phase3_prices':PRICE3SHA}}
    with Path(a.output).open('x',encoding='utf8',newline='\n') as f:json.dump(output,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'all193':total,'price_cohorts':cohorts},ensure_ascii=False))
if __name__=='__main__':main()
