import gzip, json, math, hashlib, random
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
NAMES=('market','outcome_form','market_residual')
ETA=.05
ALPHA=.01
START='2025-01-01'

def update(w, probs, y):
    # Fixed-share transition after each revealed event; log-loss exponential weights.
    z=[w[i]*max(probs[i][y],1e-15)**ETA for i in range(3)]
    s=sum(z); z=[v/s for v in z]
    return [(1-ALPHA)*v+ALPHA/3 for v in z]

def mix(weights, forecasts):
    return [sum(w*p[j] for w,p in zip(weights,forecasts)) for j in range(len(forecasts[0]))]

def calculate(rows):
    rows=sorted(rows,key=lambda r:(r['day'],r['id']))
    state={}; pending=defaultdict(list); output=[]
    for day in sorted({r['day'] for r in rows}):
        group=[r for r in rows if r['day']==day]
        for s in sorted({r['sport'] for r in group}):
            w=state.get(s,[1/3]*3)
            due=sorted((r for r in pending[s] if r['day'] <= (date.fromisoformat(day)-timedelta(days=4)).isoformat()),key=lambda r:(r['day'],r['id']))
            for r in due: w=update(w,[r['predictions'][n]['p'] for n in NAMES],r['y'])
            pending[s]=[r for r in pending[s] if r not in due]; state[s]=w
            for r in (x for x in group if x['sport']==s):
                ps=[r['predictions'][n]['p'] for n in NAMES]
                forecasts={'market':ps[0], 'equal':mix([1/3]*3,ps), 'fixed_50_50':mix([.5,.5,0],ps), 'fixed_share':mix(w,ps)}
                record={k:r[k] for k in ('id','day','sport','league','kickoff','odds','y','fallback','month_state_sha256')}
                record['experts']=dict(zip(NAMES,ps));record['weights']=list(w);record['methods']={}
                for name,p in forecasts.items():
                    pick=max(range(len(p)),key=lambda i:p[i]); hit=pick==r['y']
                    record['methods'][name]={'p':p,'pick':pick,'hit':hit,'net':r['odds'][pick]-1 if hit else -1}
                if day>=START: output.append(record)
            pending[s].extend(x for x in group if x['sport']==s)
    return output

def metrics(rows,name):
    if not rows:return None
    net=0.;peak=0.;dd=0.;hits=0;ll=0.;brier=0.
    for r in rows:
        a=r['methods'][name]; net+=a['net'];peak=max(peak,net);dd=max(dd,peak-net);hits+=a['hit']
        ll-=math.log(max(a['p'][r['y']],1e-15));brier+=sum((v-(i==r['y']))**2 for i,v in enumerate(a['p']))
    n=len(rows)
    return {'n':n,'hits':hits,'hit_rate':hits/n,'log_loss':ll/n,'brier_multiclass':brier/n,'roi':net/n,'net':net,'max_drawdown_day_order_units':dd}

def main():
    paths=[ROOT/f'experiments/all-sports-underdog-20261031/results/{s}/domestic_archive/ledger.jsonl.gz' for s in ('bs','sc','bk','vl')]
    source=[]; hashes={}; counts={}
    for p in paths:
        hashes[str(p.relative_to(ROOT)).replace('\\','/')]=hashlib.sha256(p.read_bytes()).hexdigest()
        with gzip.open(p,'rt',encoding='utf8') as f: rows=[json.loads(line) for line in f]
        counts[p.parts[-3]]={'all':len(rows),'fallback_excluded':sum(r['fallback'] for r in rows)}
        source.extend(r for r in rows if not r['fallback'])
    assert len({r['id'] for r in source})==len(source)
    result=calculate(source); names=('market','equal','fixed_50_50','fixed_share')
    subsets={'all':result}
    for r in result:
        for key in ('sport:'+r['sport'],'league:'+r['sport']+':'+r['league'],'year:'+r['day'][:4], 'quarter:'+r['day'][:4]+'Q'+str((int(r['day'][5:7])-1)//3+1)):
            subsets.setdefault(key,[]).append(r)
    summary={k:{n:metrics(v,n) for n in names} for k,v in subsets.items()}
    # Paired day-cluster bootstrap of average fixed-share minus market loss and ROI.
    days=defaultdict(list)
    for r in result:days[r['day']].append(r)
    clusters=list(days.values()); rng=random.Random(827); diffs=[]
    for _ in range(500):
        sampled=[r for __ in clusters for r in rng.choice(clusters)]
        a=metrics(sampled,'fixed_share');b=metrics(sampled,'market');diffs.append((a['log_loss']-b['log_loss'],a['roi']-b['roi']))
    ci={}
    for i,key in enumerate(('log_loss_difference','roi_difference')):
        values=sorted(x[i] for x in diffs);ci[key]=[values[12],values[487]]
    meta={'inputs_sha256':hashes,'input_counts':counts,'date_range':[result[0]['day'],result[-1]['day']],'evaluation_rows':len(result),'warmup':'eligible non-fallback before 2025; delayed outcomes update sequentially','eta':ETA,'alpha':ALPHA,'summary':summary,'paired_day_bootstrap_500_95pct':ci}
    (OUT/'summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    with (OUT/'predictions.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as z:
            for r in result:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())
    print(json.dumps({'n':len(result),'range':meta['date_range'],'all':summary['all'],'ci':ci},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
