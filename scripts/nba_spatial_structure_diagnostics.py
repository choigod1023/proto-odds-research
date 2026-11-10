"""Frozen NBA spatial vs family diagnostics and fixed-width distance profiles."""
import argparse, collections, datetime as dt, gzip, hashlib, io, json, math, pathlib, statistics
import numpy as np
from scipy.special import expit,logit

POOL_SHA='1899869c9b8a0cff51e9f2ed7cce57cf08d9a18b51213f46042d9361ebfcd691'
PRED_SHA='fd71c7ecfebb88f432cf2c4ea3ff621f993645e5ab7cec8a8df65fceb6a84a75'
TRAIN_SHA='f6e48cf01cd450bb6850d30f493bd23eb5bfd8428122f7ee793ce7fcdd79768b'
EPS=1e-6

def sha(b): return hashlib.sha256(b).hexdigest()
def checked(path,expected):
    b=path.read_bytes()
    if sha(b)!=expected: raise ValueError('SHA mismatch: '+str(path))
    return b

def weights(n,c):
    if n not in (2,3): raise ValueError('field goal only')
    c=c if isinstance(c,dict) else {};x,y=c.get('x'),c.get('y')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000 for v in (x,y)):
        return {4 if n==2 else 8:1.0}
    r=math.hypot(x-25,y);lo=max(0.,r-1.);hi=r+1.;bounds=[0.,4.,10.,16.,math.inf] if n==2 else [0.,24.,27.,math.inf];offset=0 if n==2 else 5
    result={offset+i:max(0.,min(hi,bounds[i+1])-max(lo,bounds[i]))/(hi-lo) for i in range(len(bounds)-1)}
    return {k:v for k,v in result.items() if v>0}

def hard_zone(n,c):
    c=c if isinstance(c,dict) else {};x,y=c.get('x'),c.get('y')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>1000 for v in (x,y)):return 4 if n==2 else 8
    r=math.hypot(x-25,y)
    if n==2:return 0 if r<4 else 1 if r<10 else 2 if r<16 else 3
    return 5 if r<24 else 6 if r<27 else 7

def classify(p):
    text=p.get('text','').lower();n=p.get('pointsAttempted');made=p.get('scoringPlay') is True
    if not p.get('shootingPlay') or n not in (1,2,3): return None
    if not (('makes ' in text) if made else ('misses ' in text or ' blocks ' in text)): return None
    if n==1 and 'free throw' not in text:return None
    if n in (2,3) and 'free throw' in text:return None
    if n==3 and ('two point' in text or '2-point' in text):return None
    if n==2 and ('three point' in text or '3-point' in text):return None
    if p.get('scoreValue')!=(n if made else 0):return None
    return n,made

def gzip_rows(rows):
    buf=io.BytesIO()
    with gzip.GzipFile(fileobj=buf,mode='wb',filename='',mtime=0) as z:
        for r in rows:z.write((json.dumps(r,sort_keys=True,separators=(',',':'))+'\n').encode())
    return buf.getvalue()

def soften(pool,archive):
    result=[]; max_error=0.;count=0
    for game in pool:
        gid=game['game_id'];receipt=archive/'summary'/f'{gid}.receipt.json';receipt_bytes=receipt.read_bytes();rec=json.loads(receipt_bytes)
        if rec['status']!=200 or rec['sha256']!=game['source_summary_sha256']:raise ValueError('receipt '+gid)
        d=json.loads(checked(receipt.with_name(f'{gid}.json'),game['source_summary_sha256']));comp=d['header']['competitions'][0];cs={x['homeAway']:x for x in comp['competitors']}
        if d['header']['id']!=gid or comp['date']!=game['date_utc'] or [cs[x]['team']['abbreviation'] for x in ('home','away')]!=game['teams']:raise ValueError('identity '+gid)
        ids={cs[x]['id']:i for i,x in enumerate(('home','away'))}; attempts=[[0.]*9 for _ in range(2)];makes=[[0.]*9 for _ in range(2)]; fta=[0,0];ftm=[0,0];hard_a=[[0]*9 for _ in range(2)];hard_m=[[0]*9 for _ in range(2)];seen=set()
        for p in d['plays']:
            pid=p.get('id')
            if not pid or pid in seen:raise ValueError('duplicate play '+gid)
            seen.add(pid)
            if not p.get('shootingPlay'):continue
            v=classify(p);team=p.get('team',{}).get('id')
            if v is None or team not in ids:raise ValueError('ambiguous '+gid)
            n,made=v;i=ids[team]
            if n==1:fta[i]+=1;ftm[i]+=int(made);continue
            j=hard_zone(n,p.get('coordinate'));hard_a[i][j]+=1;hard_m[i][j]+=int(made)
            for j,w in weights(n,p.get('coordinate')).items():attempts[i][j]+=w;makes[i][j]+=w*int(made)
            count+=1
        out=dict(game);out['source_receipt_sha256']=sha(receipt_bytes);out['sides']=[]
        for i,hard in enumerate(game['sides']):
            if fta[i]!=hard['fta'] or ftm[i]!=hard['ftm']:raise ValueError('ft mismatch '+gid)
            if hard_a[i]!=hard['attempts'] or hard_m[i]!=hard['makes']:raise ValueError('hard zone mismatch '+gid)
            for indices in (slice(0,5),slice(5,9),slice(0,9)):
                for key,arr in (('attempts',attempts[i]),('makes',makes[i])):
                    err=abs(sum(arr[indices])-sum(hard[key][indices]));max_error=max(max_error,err)
                    if err>1e-8:raise ValueError(f'conservation {gid} {key} {err}')
            s=dict(hard);s['attempts']=attempts[i];s['makes']=makes[i];out['sides'].append(s)
        out['zone_spec_id']='espn_xy25_0_uniform_radius_1ft_v1';out['geometry_status']='internally_consistent_unofficial';result.append(out)
    return result,{'field_goal_events':count,'max_family_conservation_error':max_error}

def arr(s):return np.array([s['estimated_possessions'],*s['attempts'],*s['makes'],s['fta'],s['ftm']],dtype=float)
def components(own,allowed,league,spatial):
    if not spatial:
        def combine(a):return np.r_[a[0],sum(a[1:6]),sum(a[6:10]),sum(a[10:15]),sum(a[15:19]),a[19:21]]
        own,allowed,league=map(combine,(own,allowed,league));n=2; fam=[slice(0,1),slice(1,2)]
    else:n=9;fam=[slice(0,5),slice(5,9)]
    p=league[0];la=league[1:1+n];lm=league[1+n:1+2*n];rates=[];made=[]
    for i in range(n):
        base_rate=la[i]/p
        if base_rate==0:rates.append(0.);made.append(0.);continue
        f=fam[0] if i<fam[0].stop else fam[1];strength=200*la[i]/sum(la[f]);base=np.clip(lm[i]/la[i],EPS,1-EPS)
        ao,ad=own[1+i],allowed[1+i];mo,md=own[1+n+i],allowed[1+n+i]
        ro=(ao+500*base_rate)/(own[0]+500);rd=(ad+500*base_rate)/(allowed[0]+500);rate=float(np.clip(ro*rd/base_rate,0,1.5))
        po=np.clip((mo+strength*base)/(ao+strength),EPS,1-EPS);pd=np.clip((md+strength*base)/(ad+strength),EPS,1-EPS)
        accuracy=float(np.clip(expit(logit(po)+logit(pd)-logit(base)),EPS,1-EPS));rates.append(rate);made.append(rate*accuracy)
    return [(sum(rates[f]),sum(made[f])) for f in fam]

def sym_decompose(qr,qm,sr,sm,poss,points):
    qa=qm/qr if qr else 0.;sa=sm/sr if sr else 0.
    rate=points*poss*(sr-qr)*(sa+qa)/2;accuracy=points*poss*(sa-qa)*(sr+qr)/2
    return rate,accuracy

def summary(xs):
    a=np.asarray(xs,float)
    return {'n':len(a),'mean':float(a.mean()),'p05':float(np.quantile(a,.05)),'median':float(np.median(a)),'p95':float(np.quantile(a,.95)),'min':float(a.min()),'max':float(a.max())}

def diagnose(pool,predictions):
    parsed=[(dt.date.fromisoformat(g['day']),g,[arr(s) for s in g['sides']]) for g in sorted(pool,key=lambda x:(x['day'],x['date_utc'],x['game_id']))]; daily={}; out=[]; maxerror=0.
    for target in predictions:
        day=dt.date.fromisoformat(target['day'])
        if day not in daily:
            active=[x for x in parsed if day-dt.timedelta(days=365)<=x[0]<=day-dt.timedelta(days=4)]
            league=np.sum([s for _,g,ss in active for s in ss],axis=0);teams=collections.defaultdict(lambda:{'own':[],'allow':[],'ids':[]})
            for _,g,ss in active:
                for i,t in enumerate(g['teams']):teams[t]['own'].append(ss[i]);teams[t]['allow'].append(ss[1-i]);teams[t]['ids'].append(g['game_id'])
            daily[day]=(league,teams)
        league,teams=daily[day]; ts=[teams[t] for t in target['teams']]
        for i,t in enumerate(ts):
            if t['ids']!=target['history_game_ids'][i]:raise ValueError('history mismatch '+target['id'])
        own=[np.sum(t['own'],axis=0) for t in ts];allowed=[np.sum(t['allow'],axis=0) for t in ts];poss=[(own[i][0]+allowed[i][0])/(2*len(ts[i]['ids'])) for i in (0,1)];P=math.sqrt(poss[0]*poss[1]);sides=[]
        for i in (0,1):
            q=components(own[i],allowed[1-i],league,False);s=components(own[i],allowed[1-i],league,True);families=[]
            for f,points in enumerate((2,3)):
                qr,qm=q[f];sr,sm=s[f];rate,accuracy=sym_decompose(qr,qm,sr,sm,P,points);err=abs(rate+accuracy-points*P*(sm-qm));maxerror=max(maxerror,err)
                families.append({'family':points,'q_rate':qr,'s_rate':sr,'rate_ratio':sr/qr if qr else None,'q_accuracy':qm/qr if qr else None,'s_accuracy':sm/sr if sr else None,'rate_points':rate,'accuracy_points':accuracy,'difference_points':points*P*(sm-qm)})
            actual=sum(f['difference_points'] for f in families);expected=target['spatial_expected_points'][i]-target['nonspatial_expected_points'][i]
            if abs(actual-expected)>1e-8:raise ValueError(f'expected points mismatch {target["id"]} {actual} {expected}')
            sides.append(families)
        qh=target['probabilities']['nonspatial']>=.5;sh=target['probabilities']['spatial']>=.5;win=bool(target['home_win']);flip=qh!=sh
        out.append({'id':target['id'],'day':target['day'],'period':target['period'],'teams':target['teams'],'home_win':target['home_win'],'probabilities':{'Q':target['probabilities']['nonspatial'],'S':target['probabilities']['spatial']},'odds':target['odds'],'flip':flip,'q_win':qh==win,'s_win':sh==win,'q_profit':target['odds'][0 if qh else 1]*int(qh==win)-1,'s_profit':target['odds'][0 if sh else 1]*int(sh==win)-1,'possessions':P,'sides':sides})
    periods={}
    for label in ('all','A','B'):
        subset=[r for r in out if label=='all' or r['period']==label];flips=[r for r in subset if r['flip']];fam={}
        for f in range(2):
            values=[side[f] for r in subset for side in r['sides']];fam[str((2,3)[f])]={'rate_ratio':summary([x['rate_ratio'] for x in values]),'rate_points':summary([x['rate_points'] for x in values]),'accuracy_points':summary([x['accuracy_points'] for x in values]),'difference_points':summary([x['difference_points'] for x in values])}
        flip_details={'mean_absolute_probability_shift':float(np.mean([abs(r['probabilities']['S']-r['probabilities']['Q']) for r in flips])) if flips else None,'mean_absolute_rate_margin_points':float(np.mean([abs(sum(x['rate_points'] for x in r['sides'][0])-sum(x['rate_points'] for x in r['sides'][1])) for r in flips])) if flips else None,'mean_absolute_accuracy_margin_points':float(np.mean([abs(sum(x['accuracy_points'] for x in r['sides'][0])-sum(x['accuracy_points'] for x in r['sides'][1])) for r in flips])) if flips else None}
        periods[label]={'flip_details':flip_details,'games':len(subset),'flips':len(flips),'q_wins_on_flips':sum(r['q_win'] for r in flips),'s_wins_on_flips':sum(r['s_win'] for r in flips),'q_profit_on_flips':sum(r['q_profit'] for r in flips),'s_profit_on_flips':sum(r['s_profit'] for r in flips),'families':fam,'home_minus_away_rate_contribution':summary([sum(x['rate_points'] for x in r['sides'][0])-sum(x['rate_points'] for x in r['sides'][1]) for r in subset]),'home_minus_away_accuracy_contribution':summary([sum(x['accuracy_points'] for x in r['sides'][0])-sum(x['accuracy_points'] for x in r['sides'][1]) for r in subset])}
    return out,{'periods':periods,'max_decomposition_error':maxerror}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pool',required=True);ap.add_argument('--model',required=True);ap.add_argument('--archive',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();pooldir=pathlib.Path(a.pool);model=pathlib.Path(a.model);archive=pathlib.Path(a.archive);out=pathlib.Path(a.out)
    pool=[json.loads(x) for x in gzip.decompress(checked(pooldir/'games.jsonl.gz',POOL_SHA)).splitlines()];pred=[json.loads(x) for x in gzip.decompress(checked(model/'predictions.jsonl.gz',PRED_SHA)).splitlines()];train=[json.loads(x) for x in gzip.decompress(checked(model/'train_features.jsonl.gz',TRAIN_SHA)).splitlines()]
    if len(pool)!=1185 or len({x['game_id'] for x in pool})!=1185 or len(pred)!=1260:raise ValueError('unexpected source size')
    soft,conservation=soften(pool,archive);detail,diag=diagnose(pool,pred);out.mkdir(parents=True,exist_ok=True);softbytes=gzip_rows(soft);detailbytes=gzip_rows(detail);(out/'soft_games.jsonl.gz').write_bytes(softbytes);(out/'diagnostic_games.jsonl.gz').write_bytes(detailbytes)
    diag.update({'input_sha256':{'hard_games':POOL_SHA,'predictions':PRED_SHA,'train_features':TRAIN_SHA},'source_games':len(pool),'train_feature_rows':len(train),'soft_sha256':sha(softbytes),'diagnostic_games_sha256':sha(detailbytes),'conservation':conservation,'soft_spec':'uniform [max(0,r-1),r+1], r=hypot(x-25,y), original family bounds, 1ft fixed; unknown preserved','geometry_status':'internally_consistent_unofficial','scope':'descriptive retrospective; no new model or test-set tuning'})
    (out/'diagnostics.json').write_text(json.dumps(diag,indent=2,sort_keys=True)+'\n',encoding='utf8');print(json.dumps(diag,indent=2))
if __name__=='__main__':main()
