"""Reconcile immutable KBO overlay forecasts and decimal settlement offline."""
import argparse
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
import math
import subprocess
import numpy as np

M='experiments/kbo-player-model-20261103/'
D='experiments/kbo-player-data-20261103/'
O='experiments/kbo-official-odds-20261103/'
MODEL='5c797509a9305674307ecce83df99ea8f9a2b0c1'
DATA='b0833a6'
PRICES='9bcbd53bf95ce6c6961b5b88fd43877152d602b6'
TEAMS={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}


def git(repo,commit,path):
    return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])


def sha(raw):return hashlib.sha256(raw).hexdigest()


def shin(prices):
    pi=[1/float(x) for x in prices]; total=sum(pi)
    if total<=1:return [x/total for x in pi]
    def f(z):return [(math.sqrt(z*z+4*(1-z)*x*x/total)-z)/(2*(1-z)) for x in pi]
    lo,hi=0.,.9
    for _ in range(200):
        mid=(lo+hi)/2; s=sum(f(mid))
        if abs(s-1)<1e-12:break
        if s>1:lo=mid
        else:hi=mid
    vals=f((lo+hi)/2);return [x/sum(vals) for x in vals]


def audit(model_repo,data_repo,price_repo):
    model=lambda name:git(model_repo,MODEL,M+name)
    before=lambda name:git(model_repo,'09b259b5',M+name)
    for f in ('adapter.py','model.py','run.py','phase2/features.jsonl.gz','phase2/development-labels.jsonl.gz','phase2/predictions.json','phase2/test-labels.jsonl.gz','phase2/results.json'):
        assert model(f)==before(f),f
    assert sha(model('phase2/predictions.json'))=='d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'
    assert sha(model('phase2/results.json'))=='8c8776c43dc0013056026c83c3b5afaf2d462a7de3e346438b2e71e536408562'
    overlay=model('phase3/overlay-results.json');assert sha(overlay)=='24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'
    result=json.loads(overlay);assert 'incorrectly describes' in result['results']['phase2_comparison_note_erratum']
    read_gzip=lambda f:[json.loads(s) for s in gzip.decompress(model('phase2/'+f)).splitlines()]
    rows={r['fixture_id']:r for r in read_gzip('features.jsonl.gz') if r['season']==2026}
    labels={r['fixture_id']:r for r in read_gzip('test-labels.jsonl.gz')}
    pred={k:{p['fixture_id']:p for p in v} for k,v in json.loads(model('phase2/predictions.json'))['predictions'].items()}
    original={r['fixture_id']:r for r in map(json.loads,git(data_repo,DATA,D+'rows.jsonl').splitlines())}
    original_labels={r['fixture_id']:r for r in map(json.loads,git(data_repo,DATA,D+'labels.jsonl').splitlines())}
    official_raw=git(price_repo,PRICES,O+'restored-prices.jsonl')
    assert sha(official_raw)=='cb556b0fb182eb13de6d22a513b8a04952e5e22d6b31a6b6ca44f543edb647aa'
    assert b'\r\n' not in official_raw and sha(official_raw.replace(b'\n',b'\r\n'))=='d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117'
    official={r['fixture_id']:r for r in map(json.loads,official_raw.splitlines())}
    old=json.loads(model('phase2/results.json'));ledger=[];counts=Counter()
    assert len(rows)==len(labels)==726
    for fid,r in rows.items():
        lab=labels[fid];ol=original_labels[fid]
        assert (lab['home_score'],lab['away_score'])==(ol['home_score'],ol['away_score'])
        market=original[fid]['market'];restore=official.get(fid)
        if market is not None:
            assert r['market']==market and r['kickoff_at'] and lab['market_id']==market['market_id']
            prices=market['selections'];clock=True;origin='original_archived'
        elif restore:
            assert restore['official_home_id']==TEAMS[r['home_id']] and restore['official_away_id']==TEAMS[r['away_id']]
            assert restore['game_at'][:10]==r['event_date'] and restore['score']==f"{lab['home_score']}:{lab['away_score']}"
            assert restore['source_observed_at'] is None and restore['execution_price_verified'] is False
            assert restore['source_kickoff_matched']==bool(r['kickoff_at'])
            prices=restore['prices'];clock=restore['source_kickoff_matched'];origin='official_restored'
        else:
            counts['no_price']+=1;continue
        assert set(prices)=={'home','away'} and all(1<float(v)<1000 for v in prices.values())
        win='home' if lab['home_score']>lab['away_score'] else 'away' if lab['home_score']<lab['away_score'] else 'draw'
        probs={'market':shin([prices['home'],prices['away']])}
        probs.update({name:[arr[fid]['probabilities_2way']['home'],arr[fid]['probabilities_2way']['away']] for name,arr in pred.items()})
        forecasts={}
        for name,ps in probs.items():
            assert min(ps)>0 and abs(sum(ps)-1)<1e-10
            pick='home' if ps[0]>=ps[1] else 'away';void=win=='draw';hit=not void and pick==win
            net=Decimal(0) if void else Decimal(str(prices[pick]))-1 if hit else Decimal(-1)
            forecasts[name]={'p':ps,'pick':pick,'void':void,'hit':hit,'net':net}
        ledger.append({'id':fid,'day':r['event_date'],'winner':win,'clock':clock,'origin':origin,'forecasts':forecasts});counts[(origin,clock)]+=1
    assert counts==Counter({('original_archived',True):354,('official_restored',False):244,('official_restored',True):85,'no_price':43})
    out={}
    for key,subset in [('strict_original_time_match',[r for r in ledger if r['clock']]),('extended_day_team_source_confirmed',ledger)]:
        saved=result['results']['cohorts'][key];recorded={r['fixture_id']:r for r in result['ledgers'][key]}
        assert len(subset)==saved['n'] and set(recorded)=={r['id'] for r in subset}
        for name in ('market',*pred):
            daily=defaultdict(Decimal);monthly=defaultdict(Decimal);hits=0;net=Decimal(0);ll=[];brier=[]
            for r in subset:
                a=r['forecasts'][name];b=recorded[r['id']]['forecasts'][name]
                assert (a['pick'],a['void'],a['hit'],a['net'])==(b['pick'],b['void'],b['hit'],Decimal(b['net']))
                assert max(abs(x-y) for x,y in zip(a['p'],b['probabilities']))<1e-12
                hits+=a['hit'];net+=a['net'];daily[r['day']]+=a['net'];monthly[r['day'][:7]]+=a['net']
                if not a['void']:
                    y=0 if r['winner']=='home' else 1;ll.append(-math.log(a['p'][y]));brier.append(sum((x-int(i==y))**2 for i,x in enumerate(a['p'])))
            bal=peak=dd=Decimal(0)
            for day in sorted(daily):bal+=daily[day];peak=max(peak,bal);dd=max(dd,peak-bal)
            stored=saved['models'][name]
            assert (stored['hits'],Decimal(stored['net_units']),Decimal(stored['max_daily_close_drawdown_units']))==(hits,net,dd)
            assert {k:str(v) for k,v in sorted(monthly.items())}==stored['monthly_units']
            assert abs(stored['logloss_decisive']-sum(ll)/len(ll))<1e-12 and abs(stored['brier_decisive']-sum(brier)/len(brier))<1e-12
            assert abs(stored['roi_including_refunds']-float(net/len(subset)))<1e-12
        for baseline in ('market','team'):
            weeks=defaultdict(lambda:np.zeros(4));changed=0
            for r in subset:
                a=r['forecasts']['player'];b=r['forecasts'][baseline];w=date.fromisoformat(r['day']).isocalendar()[:2];v=weeks[w]
                v[0]+=int(not a['void']);v[1]+=int(a['hit'])-int(b['hit']);v[2]+=1;v[3]+=float(a['net']-b['net']);changed+=a['pick']!=b['pick']
            blocks=np.array([weeks[k] for k in sorted(weeks)]);rng=np.random.default_rng(20261103)
            samples=blocks[rng.integers(0,len(blocks),size=(5000,len(blocks)))].sum(axis=1)
            ci=np.quantile(samples[:,1]/samples[:,0],[.025,.975]);roi=np.quantile(samples[:,3]/samples[:,2],[.025,.975]);stored=saved['player_vs_'+baseline]
            assert np.allclose(ci,stored['accuracy_delta_ci95']) and np.allclose(roi,stored['roi_delta_ci95']) and changed==stored['changed']
        out[key]={'n':len(subset),'void':sum(r['winner']=='draw' for r in subset),'player_hits':saved['models']['player']['hits'],'player_net':saved['models']['player']['net_units'],'market_hits':saved['models']['market']['hits'],'market_net':saved['models']['market']['net_units']}
    prior={mode:{r['fixture_id']:r for r in arr} for mode,arr in old['archived_scenario_ledger'].items()}
    for r in ledger:
        if r['origin']!='original_archived':continue
        for mode in pred:
            for name in (mode,'market'):
                a=prior[mode][r['id']]['forecasts'][name];b=r['forecasts'][name]
                assert (a['pick'],a['hit'],a['void'],Decimal(a['net']))==(b['pick'],b['hit'],b['void'],b['net'])
                assert max(abs(x-y) for x,y in zip(a['probabilities'],b['p']))<1e-12
    return {'cohorts':out,'total_settlements':sum(x['n'] for x in out.values())*(len(pred)+1),'originals_preserved':354,'date_only_2026':244,'verified_execution_roi':result['results']['verified_execution_roi']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model-repo',required=True);p.add_argument('--data-repo',required=True);p.add_argument('--price-repo',required=True);a=p.parse_args()
    print(json.dumps(audit(a.model_repo,a.data_repo,a.price_repo),ensure_ascii=False,sort_keys=True))
