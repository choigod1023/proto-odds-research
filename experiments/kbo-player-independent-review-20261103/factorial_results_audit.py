"""Independent frozen-input-to-results KBO audit; no fit, network, or writes."""
import gzip
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
import numpy as np

MODEL=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/kbo-player-model-20261103')
DATA=Path('C:/Users/user/Documents/Codex/kbodata')
ODDS=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103')
M=Path('experiments/kbo-player-model-20261103'); D=Path('experiments/kbo-player-data-20261103'); O=Path('experiments/kbo-official-odds-20261103')
N=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter')
TEAMS={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}
def sha(x): return hashlib.sha256(x).hexdigest()
def pin(repo,commit,path,expected=None,normalize=False):
    raw=(repo/path).read_bytes(); blob=subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=repo)
    assert (raw.replace(b'\r\n',b'\n') if normalize else raw)==blob,str(path)
    if expected: assert sha(raw)==expected,(str(path),sha(raw))
    return raw
def js(path): return json.loads(path.read_bytes())
def indexed(path):
    b=path.read_bytes(); b=gzip.decompress(b) if path.suffix=='.gz' else b
    values=[json.loads(x) for x in b.splitlines()]; out={x['fixture_id']:x for x in values}; assert len(out)==len(values)
    return out
def scalar(k,v):
    def valid(*v): return all(type(x) is int and x>=0 for x in v)
    if v is None or k=='park_factor': return None
    if k.endswith('_rest_days'): return float(v) if valid(v) else None
    if k.endswith('_team_strength'):
        a=[x['runs_for']-x['runs_against'] for x in v if valid(x.get('runs_for'),x.get('runs_against'))]
        return sum(a)/len(a) if a else None
    if k.endswith('_lineup_strength'):
        a=[x for player in v for x in player['history'] or [] if valid(x.get('ab'),x.get('hit'),x.get('bb')) and x['hit']<=x['ab']]
        den=sum(x['ab']+x['bb'] for x in a)
        return sum(x['hit']+x['bb'] for x in a)/den if den else None
    if k.endswith('_starter_strength'):
        a=[x for x in v['appearances'] if valid(x.get('er'),x.get('outs'))]; den=sum(x['outs'] for x in a)
        return 27*sum(x['er'] for x in a)/den if den else None
    if k.endswith('_bullpen_load'):
        games=v['games']; return float(sum(x['relief']['outs'] for x in games)) if games and all(valid((x.get('relief') or {}).get('outs')) for x in games) else None
    raise AssertionError(k)
def rates(row,features,model,player):
    out=[]
    for side,other in [('home','away'),('away','home')]:
        ks=[side+'_team_strength',other+'_team_strength',side+'_rest_days',other+'_rest_days','park_factor']
        if player: ks += [side+'_lineup_strength',other+'_starter_strength',other+'_bullpen_load']
        raw=[features[k] for k in ks]; t=model['teams']
        v=[1.,float(side=='home')]+[float(row[side+'_id']==x) for x in t]+[float(row[other+'_id']==x) for x in t]
        v += [(x-model['mean'][i])/model['scale'][i] if x is not None else 0. for i,x in enumerate(raw)]
        v += [float(x is None) for x in raw]
        assert len(v)==len(model['beta']) and model['alpha']==100 and all(x>0 for x in model['scale'])
        out.append(math.exp(math.fsum(x*y for x,y in zip(v,model['beta']))))
    return out
def probs(h,a):
    # Independent 100-term recurrence; the omitted tail is negligible here.
    hp=[math.exp(-h)]; ap=[math.exp(-a)]
    for i in range(1,100): hp.append(hp[-1]*h/i); ap.append(ap[-1]*a/i)
    return [math.fsum(hp[i]*ap[j] for i in range(100) for j in range(100) if (i>j if side==0 else i==j if side==1 else i<j)) for side in range(3)]
def close(a,b,tol=1e-9): assert abs(a-b)<tol,(a,b)
def truth(y): return 'home' if y['home_score']>y['away_score'] else 'away' if y['away_score']>y['home_score'] else 'draw'
def pick3(p): return max(('home','draw','away'),key=p.get)
def pick2(p): return 'home' if p[0]>=p[1] else 'away'
def shin(prices):
    implied=[1/float(x) for x in prices]; total=sum(implied)
    if total<=1: return [x/total for x in implied]
    def values(z): return [(math.sqrt(z*z+4*(1-z)*x*x/total)-z)/(2*(1-z)) for x in implied]
    lo=0.; hi=.9
    for _ in range(200):
        mid=(lo+hi)/2; s=sum(values(mid))
        if abs(s-1)<1e-12: break
        if s>1: lo=mid
        else: hi=mid
    v=values((lo+hi)/2); return [x/sum(v) for x in v]
def paired(ledger,a,b):
    groups=defaultdict(lambda:np.zeros(4)); changed=0
    for x in ledger:
        f=x['forecasts']; u=f[a]; v=f[b]; g=groups[date.fromisoformat(x['date']).isocalendar()[:2]]
        if not u['void']: g[0]+=1; g[1]+=int(u['hit'])-int(v['hit'])
        g[2]+=1; g[3]+=float(Decimal(u['net'])-Decimal(v['net'])); changed+=u['pick']!=v['pick']
    ar=np.array([groups[k] for k in sorted(groups)]); rng=np.random.default_rng(20261103)
    samples=ar[rng.integers(0,len(ar),size=(5000,len(ar)))].sum(axis=1)
    return len(groups),changed,np.quantile(samples[:,1]/samples[:,0],[.025,.975]),np.quantile(samples[:,3]/samples[:,2],[.025,.975])

prediction_sha='669a0016f07275ed9136aa55fdf9f17c96f2c497a32d323a3cad9a2a0dcbab17'
rawpred=pin(MODEL,'29b3ebe6',M/'factorial/predictions.json',prediction_sha)
assert subprocess.run(['git','merge-base','--is-ancestor','29b3ebe6','7130afb5'],cwd=MODEL).returncode==0
pin(MODEL,'7130afb5',M/'factorial/results.json')
for path,sha256 in [('phase2/features.jsonl.gz','346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42'),('phase2/test-labels.jsonl.gz','885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9'),('phase2/predictions.json','d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'),('phase3/overlay-results.json','24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23')]:
    pin(MODEL,'7130afb5',M/path,sha256)
source_sha={'official_batting_only':'789a413ab35575b2461dd2d6943485e88be55f96e1eab9364b6e706691ee3e58','official_starter_only':'c5481b7fd7a36bc345e11ec953225ab659431408059cbb03fedab40050df6a46','official_batting_and_starter':'f16c08073737188b9bd2dec9de59791cdeb72033e6bba8c3061061809a2a8131'}
for n,h in source_sha.items(): pin(DATA,'b2dbad4',D/f'official-full-2026-{n}.jsonl.gz',h)
pin(DATA,'b2dbad4',D/'prior-projected-features.jsonl.gz','eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae')
pin(DATA,'b2dbad4',D/'labels.jsonl','ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca')
pin(ODDS,'9c7986bc',O/'audit-original-2026/audit-original-2026-prices.jsonl.gz')
pred=json.loads(rawpred); result=js(MODEL/M/'factorial/results.json'); phase2=js(MODEL/M/'phase2/predictions.json'); base=indexed(MODEL/M/'phase2/features.jsonl.gz'); prior=indexed(DATA/D/'prior-projected-features.jsonl.gz')
labels=indexed(MODEL/M/'phase2/test-labels.jsonl.gz'); rawlabels=indexed(DATA/D/'labels.jsonl'); overlays={n:indexed(DATA/D/f'official-full-2026-{n}.jsonl.gz') for n in N[1:]}
oldprices=js(MODEL/M/'phase3/overlay-results.json'); audits=indexed(ODDS/O/'audit-original-2026/audit-original-2026-prices.jsonl.gz')
pin(MODEL,'7130afb5',M/'phase4/results.json','71267f3ef9e8fea128dc7faec27530b1a9427f94691995a07d2a4688f40b0fc0')
h1=js(MODEL/M/'phase4/results.json')
assert len(audits)==354 and len(pred['predictions'])==709 and result['prediction_sha256']==prediction_sha
assert pred['eligible_pregame'] is False and result['actual_wallet_replay'] is False
for fid,y in labels.items(): assert (y['home_score'],y['away_score'])==(rawlabels[fid]['home_score'],rawlabels[fid]['away_score'])
for fid in (x['fixture_id'] for x in pred['predictions']):
    for key,item in base[fid]['features'].items():
        v=scalar(key,prior[fid]['features'][key]['value']); got=item['value']
        if v is None: assert got is None
        else: close(v,got,1e-12)
errors=[0.,0.,0.]
for x in pred['predictions']:
    fid=x['fixture_id']; b=base[fid]; values={k:v['value'] for k,v in b['features'].items()}
    for n in N+('frozen_team',):
        f=dict(values)
        if n not in ('original_projected','frozen_team'):
            for k in ('home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength'):
                if ('lineup' in k and n in ('official_batting_only','official_batting_and_starter')) or ('starter' in k and n in ('official_starter_only','official_batting_and_starter')): f[k]=scalar(k,overlays[n][fid]['features'][k]['value'])
        rate=rates(b,f,phase2['models']['team' if n=='frozen_team' else 'player'],n!='frozen_team'); q=x['frozen_team'] if n=='frozen_team' else x['settings'][n]
        errors[0]=max(errors[0],abs(rate[0]-q['home_rate']),abs(rate[1]-q['away_rate']))
        p=probs(*rate); errors[1]=max(errors[1],*[abs(p[i]-q['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))])
        d=p[0]+p[2]; errors[2]=max(errors[2],abs(p[0]/d-q['probabilities_2way']['home']),abs(p[2]/d-q['probabilities_2way']['away']))
assert errors[0]<1e-10 and max(errors[1:])<1e-9

@lru_cache(maxsize=None)
def detail(path,expected,commit):
    compressed=pin(ODDS,commit,O/path); b=gzip.decompress(compressed); assert sha(b)==expected
    j=json.loads(b); t=j['compSchedules']; return str(j['gmTs']),[dict(zip(t['keys'],r)) for r in t['datas']]
for fid,a in audits.items():
    assert a['status']=='equal' and a['original_prices']==a['official_prices'] and a['score']==[labels[fid]['home_score'],labels[fid]['away_score']]
    mid=a['market_id'].split(':'); ts,rr=detail('audit-original-2026/'+a['response_file'],a['response_sha256'],'9c7986bc'); assert int(ts[2:])==int(mid[2])
    found=[r for r in rr if int(r['matchSeq'])==int(mid[3])]; assert len(found)==1; r=found[0]
    assert r['leagueCode']=='BS001' and r['homeId']==TEAMS[a['home_id']] and r['awayId']==TEAMS[a['away_id']]
    assert {'home':r['winAllot'],'away':r['loseAllot']}==a['original_prices'] and str(r['buyReject'])==a['official_result']['buyReject'] and r['mchScore']==a['official_result']['score']
    assert str(r['gameReject'])=='0' and str(r['gameResult'])==a['official_result']['gameResult'] and r['drawAllot']==0 and r.get('gameSubject') in ('',None)
    assert (r.get('betId')=='2' and r.get('betTypId')=='2') or (r.get('betId') is None and r.get('betTypId') is None and r.get('handi')==0)
    assert base[fid]['market']['market_id']==a['market_id'] and base[fid]['market']['selections']==a['original_prices']
    assert datetime.fromtimestamp(r['gameDate']/1000,timezone(timedelta(hours=9))).isoformat()==a['kickoff_at']
assert Counter(a['official_result']['buyReject'] for a in audits.values())=={'0':347,'1':3,'4':4}

groups={'full_709':pred['predictions'],'original_193':[x for x in pred['predictions'] if x['cohort']=='original_193'],'new_516':[x for x in pred['predictions'] if x['cohort']=='new_complement']}
summary={}
for group,items in groups.items():
    res=result['results'][group]; summary[group]={'n':len(items),'hits':{},'prices':{}}
    for n in N+('frozen_team',):
        records=[]
        for x in items:
            p=(x['frozen_team'] if n=='frozen_team' else x['settings'][n])['probabilities_3way']; y=truth(labels[x['fixture_id']]); records.append((int(pick3(p)==y),-math.log(p[y]),sum((p[k]-int(k==y))**2 for k in ('home','draw','away'))))
        metric=res['threeway']['models'][n]; summary[group]['hits'][n]=sum(z[0] for z in records)
        assert metric['n']==len(items) and metric['hits']==summary[group]['hits'][n]
        for i,k in [(0,'accuracy_3way'),(1,'logloss_3way'),(2,'brier_3way')]: close(sum(z[i] for z in records)/len(items),metric[k],1e-12)
        if group=='original_193' and n in ('original_projected','official_batting_only'):
            old=h1['all_193']['models']['original_player' if n=='original_projected' else 'counterfactual_player']
            assert metric['hits']==old['hits']
            for k in ('accuracy_3way','logloss_3way','brier_3way'): close(metric[k],old[k],1e-12)
    for n in N[1:]:
        buckets=defaultdict(lambda:np.zeros(4)); changed3=changed2=0; d3=[];d2=[]
        for x in items:
            y=truth(labels[x['fixture_id']]); a=x['settings'][n]; b=x['settings'][N[0]]; p=a['probabilities_3way'];q=b['probabilities_3way'];
            changed3+=pick3(p)!=pick3(q);changed2+=pick2([a['probabilities_2way'][k] for k in ('home','away')])!=pick2([b['probabilities_2way'][k] for k in ('home','away')])
            d3.extend(abs(p[k]-q[k]) for k in ('home','draw','away')); d2.extend(abs(a['probabilities_2way'][k]-b['probabilities_2way'][k]) for k in ('home','away'))
            metrics=lambda z: (int(pick3(z)==y),-math.log(z[y]),sum((z[k]-int(k==y))**2 for k in ('home','draw','away')))
            aa=metrics(p);bb=metrics(q); buckets[date.fromisoformat(x['event_date']).isocalendar()[:2]] += np.array([1,*[i-j for i,j in zip(aa,bb)]])
        ar=np.array([buckets[k] for k in sorted(buckets)]);rng=np.random.default_rng(20261103); draw=ar[rng.integers(0,len(ar),size=(5000,len(ar)))].sum(axis=1); ref=res['threeway']['versus_original'][n]
        assert (ref['weeks'],ref['changed_3way_picks'],ref['changed_2way_picks'])==(len(ar),changed3,changed2)
        for i,k in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1):
            for a,b in zip(np.quantile(draw[:,i]/draw[:,0],[.025,.975]),ref[k]):close(a,b,1e-12)
        close(sum(d3)/len(d3),ref['mean_absolute_probability_change_3way_per_class'],1e-12);close(sum(d2)/len(d2),ref['mean_absolute_probability_change_2way_per_class'],1e-12)
    for pc, ledger in result['price_ledgers'][group].items():
        pr=res['prices'][pc]; groupids={x['fixture_id'] for x in items}; phase3={x['fixture_id']:x for x in oldprices['ledgers'][pc] if x['fixture_id'] in groupids}
        assert len(ledger)==pr['n']==len(phase3) and {x['fixture_id'] for x in ledger}==set(phase3) and pr['cohort_denominator']==len(items)
        assert pr['refunds']==sum(x['winner']=='draw' for x in ledger)
        for x in ledger:
            fid=x['fixture_id']; y=truth(labels[fid]); assert x['winner']==y and x['price_source']==phase3[fid]['price_source']
            source=x['price_source'];
            if pc=='strict_original_time_match': assert source['kickoff_matched'] is True
            if source['origin']=='original_archived': assert fid in audits and source['prices']==audits[fid]['original_prices']
            else:
                e=source['official_evidence']; ts,rr=detail(e['response_file'],e['response_sha256'],'9c7986bc'); rr=[r for r in rr if int(r['matchSeq'])==int(e['match_seq'])]; assert len(rr)==1; r=rr[0]
                assert ts==str(e['round_id']) and str(r['buyReject'])=='0' and str(r['gameReject'])=='0' and source['prices']=={'home':r['winAllot'],'away':r['loseAllot']}
                assert r['leagueCode']=='BS001' and r['homeId']==TEAMS[x['home_id']] and r['awayId']==TEAMS[x['away_id']] and r['drawAllot']==0
                assert r['mchScore']==f"{labels[fid]['home_score']}:{labels[fid]['away_score']}"
                t=datetime.fromtimestamp(r['gameDate']/1000,timezone(timedelta(hours=9))); assert t.isoformat()==e['game_at'] and t.date().isoformat()==x['date']
                if source['kickoff_matched']: assert t.isoformat()==base[fid]['kickoff_at']
            for n in N+('team','market'):
                f=x['forecasts'][n]; p=f['probabilities']; choice=pick2(p); win=choice==y; net=Decimal(0) if y=='draw' else Decimal(str(source['prices'][choice]))-1 if win else Decimal(-1)
                assert f['pick']==choice and f['void']==(y=='draw') and f['hit']==win and Decimal(f['net'])==net
                z=next(z for z in items if z['fixture_id']==fid)
                if n in N:
                    assert all(abs(p[i]-z['settings'][n]['probabilities_2way'][k])<1e-12 for i,k in enumerate(('home','away')))
                if n=='team': assert all(abs(p[i]-z['frozen_team']['probabilities_2way'][k])<1e-12 for i,k in enumerate(('home','away')))
                if n=='market': assert all(abs(a-b)<1e-12 for a,b in zip(p,shin([source['prices']['home'],source['prices']['away']])))
        summary[group]['prices'][pc]={'n':len(ledger),'refunds':pr['refunds'],'net':{}}
        for n in N+('team','market'):
            daily=defaultdict(Decimal);monthly=defaultdict(Decimal); net=Decimal(0); hits=void=0; loss=[];brier=[]
            for x in ledger:
                f=x['forecasts'][n]; amount=Decimal(f['net']); net+=amount; daily[x['date']]+=amount;monthly[x['date'][:7]]+=amount;hits+=f['hit'];void+=f['void']
                if not f['void']:
                    y=0 if x['winner']=='home' else 1; loss.append(-math.log(f['probabilities'][y])); brier.append(sum((p-int(i==y))**2 for i,p in enumerate(f['probabilities'])))
            balance=peak=dd=Decimal(0)
            for d in sorted(daily): balance+=daily[d]; peak=max(peak,balance); dd=max(dd,peak-balance)
            ref=pr['models'][n];assert (ref['bets'],ref['hits'],ref['voids'],ref['decisive'])==(len(ledger),hits,void,len(ledger)-void)
            assert Decimal(ref['net_units'])==net and Decimal(ref['max_daily_close_drawdown_units'])==dd and {k:Decimal(v) for k,v in ref['monthly_units'].items()}==monthly
            close(float(net/len(ledger)),ref['roi_including_refunds'],1e-12);close(hits/(len(ledger)-void),ref['accuracy_decisive'],1e-12);close(sum(loss)/len(loss),ref['logloss_decisive'],1e-12);close(sum(brier)/len(brier),ref['brier_decisive'],1e-12)
            summary[group]['prices'][pc]['net'][n]=str(net)
            if group=='original_193' and n in ('original_projected','official_batting_only'):
                assert ref==h1['price_cohorts'][pc]['models']['original_player' if n=='original_projected' else 'counterfactual_player']
        for category,baseline,names in [('versus_original',N[0],N[1:]),('versus_market','market',N),('versus_team','team',N)]:
            for n in names:
                weeks,changed,acc,roi=paired(ledger,n,baseline); ref=pr[category][n];assert (weeks,changed)==(ref['weeks'],ref['changed'])
                for a,b in zip(acc,ref['accuracy_delta_ci95']):close(a,b,1e-12)
                for a,b in zip(roi,ref['roi_delta_ci95']):close(a,b,1e-12)
        zero=[x for x in ledger if x['price_source']['origin']=='official_restored' or audits[x['fixture_id']]['official_result']['buyReject']=='0']
        summary[group]['prices'][pc]['buyReject0_sensitivity']={'n':len(zero),'refunds':sum(x['winner']=='draw' for x in zero),'excluded_nonzero':len(ledger)-len(zero),'net':{n:str(sum((Decimal(x['forecasts'][n]['net']) for x in zero),Decimal(0))) for n in N+('team','market')}}
print(json.dumps({'status':'PASS','prediction_max_error':errors,'summary':summary},ensure_ascii=False,sort_keys=True))
