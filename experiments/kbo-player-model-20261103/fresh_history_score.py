"""Post-freeze matched scoring for old and repaired player histories."""
import argparse,collections,gzip,hashlib,importlib.util,json,math,subprocess,sys
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
OLD_SHA='669a0016f07275ed9136aa55fdf9f17c96f2c497a32d323a3cad9a2a0dcbab17';OLD_COMMIT='29b3ebe623b2260d28139ebe3ef422ed442cfacf';LABEL_SHA='885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9';PRICE_SHA='24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'
SETTINGS=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter');NAMES=tuple(prefix+k for prefix in ('old_','fresh_') for k in SETTINGS)

def read_checked(path,sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('SHA mismatch: '+str(path))
    return raw

def frozen(path,sha,commit,gitpath):
    raw=read_checked(path,sha)
    if subprocess.check_output(['git','show',commit+':'+gitpath])!=raw:raise ValueError('frozen Git blob mismatch')
    return json.loads(raw)

def load(fresh_sha,fresh_commit):
    # Do not read outcomes or prices until both prediction blobs pass their checks.
    fresh=frozen(P/'fresh-history/predictions.json',fresh_sha,fresh_commit,'experiments/kbo-player-model-20261103/fresh-history/predictions.json')
    old=frozen(P/'factorial/predictions.json',OLD_SHA,OLD_COMMIT,'experiments/kbo-player-model-20261103/factorial/predictions.json')
    if fresh['historical_receipt_verified'] is not False or fresh['eligible_pregame'] is not False:raise ValueError('invalid retrospective availability')
    fs={x['fixture_id']:x for x in fresh['predictions']};os={x['fixture_id']:x for x in old['predictions']}
    if len(fs)!=709 or len(os)!=709 or set(fs)!=set(os):raise ValueError('cohort mismatch')
    for fid in fs:
        if fs[fid]['event_date']!=os[fid]['event_date'] or fs[fid]['cohort']!=os[fid]['cohort'] or set(fs[fid]['settings'])!=set(SETTINGS):raise ValueError('fresh identity or settings mismatch')
    labels=[json.loads(z) for z in gzip.decompress(read_checked(P/'phase2/test-labels.jsonl.gz',LABEL_SHA)).splitlines()]
    if len(labels)!=726 or len({x['fixture_id'] for x in labels})!=726 or any(x['status']!='final' or any(type(x[k]) is not int or x[k]<0 for k in ('home_score','away_score')) for x in labels):raise ValueError('invalid labels')
    prices=json.loads(read_checked(P/'phase3/overlay-results.json',PRICE_SHA))
    return fs,os,{x['fixture_id']:x for x in labels},prices

def paired_three(records,name):
    groups=collections.defaultdict(lambda:np.zeros(4));changed3=changed2=0;diff3=[];diff2=[];old='old_'+name;fresh='fresh_'+name
    for x in records:
        a=x['forecasts'][fresh];b=x['forecasts'][old];pa=a['three'];pb=b['three'];qa=a['two'];qb=b['two'];changed3+=max(('home','draw','away'),key=pa.get)!=max(('home','draw','away'),key=pb.get);changed2+=max(('home','away'),key=qa.get)!=max(('home','away'),key=qb.get);diff3.extend(abs(pa[k]-pb[k]) for k in ('home','draw','away'));diff2.extend(abs(qa[k]-qb[k]) for k in ('home','away'));groups[date.fromisoformat(x['date']).isocalendar()[:2]]+=np.array([1,*[a['scores'][i]-b['scores'][i] for i in range(3)]])
    result={'weeks':len(groups),'changed_3way_picks':changed3,'changed_2way_picks':changed2,'mean_abs_probability_3way':float(np.mean(diff3)),'mean_abs_probability_2way':float(np.mean(diff2))}
    if len(groups)>=2:
        arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);draw=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
        result.update({key:np.quantile(draw[:,i]/draw[:,0],[.025,.975]).tolist() for i,key in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1)})
    return result

def threeway(fs,os,labels,ids):
    records=[];sums={k:np.zeros(3) for k in NAMES+('team',)}
    for fid in sorted(ids,key=lambda fid:(os[fid]['event_date'],fid)):
        a=fs[fid];b=os[fid];y=labels[fid];truth='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';ps={**{'old_'+k:b['settings'][k] for k in SETTINGS},**{'fresh_'+k:a['settings'][k] for k in SETTINGS},'team':b['frozen_team']};r={'fixture_id':fid,'date':b['event_date'],'forecasts':{}}
        for name,pred in ps.items():
            p=pred['probabilities_3way'];score=(int(max(('home','draw','away'),key=p.get)==truth),-math.log(max(p[truth],1e-15)),sum((p[k]-(k==truth))**2 for k in ('home','draw','away')));sums[name]+=score;r['forecasts'][name]={'three':p,'two':pred['probabilities_2way'],'scores':score}
        records.append(r)
    n=len(records);return {'n':n,'models':{k:{'hits':int(v[0]),'accuracy_3way':float(v[0]/n),'logloss_3way':float(v[1]/n),'brier_3way':float(v[2]/n)} for k,v in sums.items()},'fresh_vs_corresponding_old':{k:paired_three(records,k) for k in SETTINGS}}

def price_results(fs,os,labels,ids,prices,overlay):
    results={};ledgers={}
    for pc,source in prices['ledgers'].items():
        ledger=[]
        for row in source:
            fid=row['fixture_id']
            if fid not in ids:continue
            y=labels[fid];winner='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
            if winner!=row['winner']:raise ValueError('price outcome mismatch')
            forecasts={k:row['forecasts'][k] for k in ('team','market')}
            for prefix,rec in (('old_',os[fid]),('fresh_',fs[fid])):
                for k,pred in rec['settings'].items():
                    p=pred['probabilities_2way'];vec=[p['home'],p['away']];pick=('home','away')[max(range(2),key=lambda i:vec[i])];void=winner=='draw';hit=not void and pick==winner;net=Decimal(0) if void else Decimal(str(row['price_source']['prices'][pick]))-1 if hit else Decimal(-1);forecasts[prefix+k]={'probabilities':vec,'pick':pick,'void':void,'hit':hit,'net':str(net)}
            for a,b in ((forecasts['old_original_projected'],row['forecasts']['player']),(forecasts['team'],{'probabilities':[os[fid]['frozen_team']['probabilities_2way'][k] for k in ('home','away')]})):
                if any(abs(x-y)>1e-12 for x,y in zip(a['probabilities'],b['probabilities'])):raise ValueError('price model mismatch')
            ledger.append({**row,'forecasts':forecasts})
        results[pc]={'n':len(ledger),'denominator':len(ids),'refunds':sum(x['winner']=='draw' for x in ledger),'source_counts':dict(collections.Counter(x['price_source']['origin'] for x in ledger)),'date_only_restored':sum(not x['price_source']['kickoff_matched'] for x in ledger),'models':{k:overlay.metrics(ledger,k) for k in NAMES+('team','market')},'fresh_vs_corresponding_old':{k:overlay.paired(ledger,'fresh_'+k,'old_'+k) for k in SETTINGS},'vs_market':{k:overlay.paired(ledger,k,'market') for k in NAMES},'vs_team':{k:overlay.paired(ledger,k,'team') for k in NAMES}}
        ledgers[pc]=ledger
    return results,ledgers

def main():
    p=argparse.ArgumentParser();p.add_argument('--fresh-sha256',required=True);p.add_argument('--fresh-commit',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    fs,os,labels,prices=load(args.fresh_sha256,args.fresh_commit)
    spec=importlib.util.spec_from_file_location('_fresh_phase3',P/'phase3_overlay.py');overlay=importlib.util.module_from_spec(spec);sys.modules[spec.name]=overlay;spec.loader.exec_module(overlay)
    allids=set(os);groups={'full_709':allids,'original_193':{k for k,v in os.items() if v['cohort']=='original_193'},'new_516':{k for k,v in os.items() if v['cohort']=='new_complement'},'through_2026_07_26':{k for k,v in os.items() if v['event_date']<='2026-07-26'},'after_2026_07_26':{k for k,v in os.items() if v['event_date']>'2026-07-26'}}
    if [len(groups[k]) for k in ('full_709','original_193','new_516','after_2026_07_26')]!=[709,193,516,239] or groups['through_2026_07_26']|groups['after_2026_07_26']!=allids:raise ValueError('unexpected cohort split')
    results={};ledgers={}
    for name,ids in groups.items():
        score=threeway(fs,os,labels,ids);price,ledger=price_results(fs,os,labels,ids,prices,overlay);results[name]={'threeway':score,'prices':price};ledgers[name]=ledger
    output={'scenario':'retrospective_source_repair_fixed_model_not_pit','fresh_prediction_sha256':args.fresh_sha256,'fresh_prediction_commit':args.fresh_commit,'old_prediction_sha256':OLD_SHA,'labels_sha256':LABEL_SHA,'prices_sha256':PRICE_SHA,'actual_wallet_replay':False,'historical_receipt_verified':False,'overlap_note':'pre/post groups overlap original 193 and new 516','group_intersections':{name:{k:len(ids&v) for k,v in groups.items()} for name,ids in groups.items()},'results':results,'price_ledgers':ledgers}
    with Path(args.output).open('x',encoding='utf8',newline='\n') as fp:json.dump(output,fp,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);fp.write('\n')
    print(json.dumps({k:{'n':len(v),'strict':results[k]['prices']['strict_original_time_match']['n'],'extended':results[k]['prices']['extended_day_team_source_confirmed']['n']} for k,v in groups.items()}))
if __name__=='__main__':main()
