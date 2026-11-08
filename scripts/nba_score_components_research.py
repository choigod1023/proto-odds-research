import os
for n in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[n]='1'
import gzip,hashlib,json,math,sys
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
from scipy.stats import poisson
import nba_score_components_espn_snapshot as old

ROOT=Path(__file__).resolve().parents[1]
CACHE=Path(os.environ.get('NBA_ESPN_CACHE','C:/Users/user/Documents/Codex/nba-espn-history-20261108e/.local-test/nba-espn-history-20261108'))
PREV=Path(os.environ.get('NBA_PREVIOUS_ARTIFACTS','C:/Users/user/Documents/Codex/nba-espn-history-20261108e/experiments/nba-espn-history-20261108'))
OUT=Path(os.environ.get('NBA_COMPONENTS_OUTPUT',str(ROOT/'experiments/nba-score-components-20261108')))

def box(game):
    path=CACHE/'summary'/f"{game['id']}.json"
    if not path.exists() or game.get('summary_invalid'): return None
    data=old.verified_json(path); out=[]
    try:
        by={b['team']['abbreviation']:b for b in data['boxscore']['teams']}
        for team,s,score in zip(game['teams'],game['stats'],game['scores']):
            v={z['name']:z['displayValue'] for z in by[team]['statistics']}
            m3,a3=map(int,v['threePointFieldGoalsMade-threePointFieldGoalsAttempted'].split('-'))
            mf,af=map(int,v['freeThrowsMade-freeThrowsAttempted'].split('-'))
            a2=s['fga']-a3; m2=s['fgm']-m3; p=s['fga']-s['orb']+s['tov']+.44*af
            if min(a2,m2,a3,m3,af,mf,s['orb'],s['tov'])<0 or m2>a2 or m3>a3 or mf>af or p<=0 or s['fga']!=a2+a3 or score!=2*m2+3*m3+mf: return None
            out.append(np.array([p,a2,m2,a3,m3,af,mf],dtype=float))
        return out
    except (KeyError,ValueError,TypeError,IndexError): return None

def prior(games,cutoff):
    arr=np.sum([x for g in games if '2025-01-01'<=g['day']<=cutoff and (b:=box(g)) for x in b],axis=0)
    return np.array([arr[1]/arr[0],arr[2]/arr[1],arr[3]/arr[0],arr[4]/arr[3],arr[5]/arr[0],arr[6]/arr[5]])

def rates(arr,pr):
    p,a2,m2,a3,m3,af,mf=arr
    nums=[a2,m2,a3,m3,af,mf]; den=[p,a2,p,a3,p,af]; strength=[500,200,500,200,500,200]
    return (np.array(nums)+pr*strength)/(np.array(den)+strength)

def probability(histories,pr):
    own=[]; defense=[]; possessions=[]
    for history in histories:
        o=np.sum([x[0] for x in history],axis=0); d=np.sum([x[1] for x in history],axis=0)
        own.append(rates(o,pr));defense.append(rates(d,pr));possessions.append((o[0]+d[0])/20)
    p=math.sqrt(possessions[0]*possessions[1]); distributions=[]
    for i in range(2):
        r=np.clip(own[i]*defense[1-i]/pr,[.01,.2,.01,.2,.01,.4],[1.5,.8,1.5,.8,1.5,.95])
        means=p*r[[0,2,4]]*r[[1,3,5]]
        dist=np.array([1.]);
        for mean,weight in zip(means,[2,3,1]):
            assert poisson.sf(250,mean)<1e-12
            mass=np.zeros(250*weight+1);mass[::weight]=poisson.pmf(np.arange(251),mean)
            dist=np.convolve(dist,mass)
        distributions.append(dist)
    h,a=distributions; return float(np.dot(h,np.cumsum(a)-.5*a))

def calc(rows,key):
    y=np.array([r['home_win'] for r in rows]); q=np.clip([r[key] for r in rows],1e-12,1-1e-12); side=q<.5; win=(side==1-y); odds=np.array([r['odds'][int(s)] for r,s in zip(rows,side)]); profit=win*odds-1
    days=defaultdict(float)
    for r,v in zip(rows,profit):days[r['day']]+=v
    cumulative=np.cumsum([days[k] for k in sorted(days)]); peak=np.maximum.accumulate(np.r_[0,cumulative])[1:]
    return dict(n=len(rows),wins=int(sum(win)),hit_rate=float(np.mean(win)),roi=float(np.mean(profit)),log_loss=float(-np.mean(y*np.log(q)+(1-y)*np.log(1-q))),brier=float(np.mean((q-y)**2)),mean_odds=float(np.mean(odds)),daily_mdd=float(np.max(peak-cumulative)))

def bootstrap(rows,key,ref):
    groups=defaultdict(list)
    for r in rows:groups[r['day']].append(r)
    days=sorted(groups); rng=np.random.default_rng(20261108); dif=[]
    for _ in range(2000):
        sample=[r for i in rng.integers(0,len(days),len(days)) for r in groups[days[i]]]
        a=calc(sample,key);b=calc(sample,ref);dif.append([a[k]-b[k] for k in ('hit_rate','roi','log_loss','brier')])
    return {k:np.percentile(np.array(dif)[:,i],[2.5,97.5]).tolist() for i,k in enumerate(('hit_rate','roi','log_loss','brier'))}

def latest_ids(history, target_day):
    return [g['id'] for g in history if (old.date.fromisoformat(target_day)-old.date.fromisoformat(g['day'])).days>=4][-10:]

def deterministic_gzip(payload):
    return gzip.compress(payload,mtime=0)

def main():
    old.CACHE=CACHE
    receipt_digest=old.receipt_digest()
    events,duplicates,conflicts=old.read_events(CACHE)
    games=[g for e in events.values() if (g:=old.parse_game(e,CACHE))]; games.sort(key=lambda g:(g['day'],g['utc'],g['id'])); byid={g['id']:g for g in games}
    team_games=defaultdict(list)
    for g in games:
        for team in g['teams']: team_games[team].append(g)
    boxes={g['id']:box(g) for g in games}; priors={k:prior(games,c) for k,c in [('validation','2025-08-28'),('evaluation','2025-12-28')]}
    rows=[json.loads(s) for s in gzip.open(PREV/'predictions.jsonl.gz','rt',encoding='utf8')]; excluded=Counter(); kept=[]
    prices=defaultdict(list)
    with gzip.open(ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            price=json.loads(line)
            if price['league']=='NBA':prices[price['id']].append(price)
    for r in rows:
        g=byid[r['espn_id']]; assert [g['day'],g['teams'],g['scores']]==[r['day'],r['teams'],r['scores']]
        match=prices[r['id']]
        if len(match)!=1: raise ValueError(f'nonunique archive price ID {r["id"]}')
        price=match[0]
        if [price['day'],old.name(price['home']),old.name(price['away']),price['odds'],int(price['y']==0)] != [r['day'],*r['teams'],r['odds'],r['home_win']]: raise ValueError(f'archive mismatch {r["id"]}')
        histories=[]; valid=True
        for team,ids in zip(g['teams'],r['history_event_ids']):
            expected=latest_ids(team_games[team],r['day'])
            if ids!=expected: raise ValueError(f'latest ten identity mismatch {g["id"]} {team}')
        for team,ids in zip(g['teams'],r['history_event_ids']):
            hist=[]
            for gid in ids:
                src=byid[gid]; b=boxes[gid]
                assert team in src['teams'] and (old.date.fromisoformat(r['day'])-old.date.fromisoformat(src['day'])).days>=4
                if b is None:valid=False; break
                i=src['teams'].index(team);hist.append((b[i],b[1-i]))
            histories.append(hist)
        if not valid:excluded['missing_or_invalid_component_box']+=1;continue
        period='validation' if r['day']<'2026-01-01' else 'evaluation'; r['components']=probability(histories,priors[period]); r['prior_efficiency_selected_side']=r.pop('selected_side'); r['prior_efficiency_selected_probability']=r.pop('selected_probability'); r['components_selected_side']='home' if r['components']>=.5 else 'away'; r['components_selected_probability']=max(r['components'],1-r['components']);r['period']=period; r['probabilities']={'market':r['market_home'],'prior_efficiency':r['candidate'],'prior_outcome_form':r['existing_outcome_form'],'components':r['components']};kept.append(r)
    models=['components','market_home','candidate','existing_outcome_form']; result={}
    for period in ('validation','evaluation'):
        subset=[r for r in kept if r['period']==period]; result[period]={'actual_date_range':[min(r['day'] for r in subset),max(r['day'] for r in subset)],'coverage':{'prior_comparison_rows':sum(r['day']<'2026-01-01' if period=='validation' else r['day']>='2026-01-01' for r in rows),'retained':len(subset)},'models':{m:calc(subset,m) for m in models},'monthly':{},'comparisons':{}}
        for month in sorted(set(r['day'][:7] for r in subset)):
            group=[r for r in subset if r['day'].startswith(month)];result[period]['monthly'][month]={m:calc(group,m) for m in models}
        for m in models[1:]:result[period]['comparisons'][m]={'changed_picks':sum((r['components']>=.5)!=(r[m]>=.5) for r in subset),'paired_date_bootstrap_95':bootstrap(subset,'components',m)}
    OUT.mkdir(exist_ok=True,parents=True)
    payload=''.join(json.dumps(dict(r,period=('A' if r['period']=='validation' else 'B')),ensure_ascii=False,sort_keys=True)+'\n' for r in kept).encode('utf8')
    (OUT/'predictions.jsonl.gz').write_bytes(deterministic_gzip(payload))
    (OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    manifest={'prior_commit':'83928e36','parser_source_sha256':hashlib.sha256((ROOT/'scripts/nba_score_components_espn_snapshot.py').read_bytes()).hexdigest(),'prior_predictions_sha256':hashlib.sha256((PREV/'predictions.jsonl.gz').read_bytes()).hexdigest(),'training_cutoffs':{'validation':'2025-08-28','evaluation':'2025-12-28'},'verified_receipt_digest':receipt_digest,'source_receipts':json.loads((PREV/'source_receipts.json').read_text(encoding='utf8')),'games':len(games),'complete_component_boxes':sum(x is not None for x in boxes.values()),'duplicate_scoreboards':duplicates,'conflicting_ids':conflicts,'excluded':dict(excluded),'price_archive_sha256':hashlib.sha256((ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz').read_bytes()).hexdigest(),'priors':{k:v.tolist() for k,v in priors.items()},'prior_order':['2pa_per_estimated_opportunity','2p_accuracy','3pa_per_estimated_opportunity','3p_accuracy','fta_per_estimated_opportunity','ft_accuracy']}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
