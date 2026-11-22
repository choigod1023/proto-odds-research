"""Causal retrospective NBA shot-profile study; isolated research only."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[name]='1'
import gzip,hashlib,json,math
from collections import defaultdict,Counter
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit,logit
from scipy.stats import poisson
import nba_conserved_espn_snapshot as old

ROOT=Path(__file__).resolve().parents[1]
CACHE=Path(os.environ.get('NBA_ESPN_CACHE','C:/Users/user/Documents/Codex/nba-espn-history-20261108e/.local-test/nba-espn-history-20261108'))
PREV=Path(os.environ.get('NBA_PREVIOUS_ARTIFACTS','C:/Users/user/Documents/Codex/nba-espn-history-20261108e/experiments/nba-espn-history-20261108'))
POOL=Path(os.environ.get('NBA_SHOT_POOL','C:/Users/user/Documents/Codex/nba-shot-profile-data-20261108/experiments/nba-shot-profile-data-20261108'))
OUT=Path(os.environ.get('NBA_SHOT_OUTPUT',str(ROOT/'experiments/nba-shot-profile-model-20261108')))
PRICE=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
EPS=1e-6

def arr(stats):
    # Canonical layout: P, nine attempts, nine makes, FTA, FTM.
    return np.r_[stats['estimated_possessions'],stats['attempts'],stats['makes'],stats['fta'],stats['ftm']].astype(float)

def aggregate(own,allowed,league,spatial):
    # own, allowed and league are sums of canonical arrays.
    if not spatial:
        def pool(x):return np.r_[x[0],sum(x[1:6]),sum(x[6:10]),sum(x[10:15]),sum(x[15:19]),x[19:21]]
        own,allowed,league=map(pool,(own,allowed,league)); n=2; fam=[slice(0,1),slice(1,2)]
    else:n=9;fam=[slice(0,5),slice(5,9)]
    p=league[0]; la=league[1:1+n]; lm=league[1+n:1+2*n]
    if p<=0 or any(sum(la[f])<=0 for f in fam) or league[-2]<=0:raise ValueError('prior_denominator')
    makes=[]
    for i in range(n):
        rate=la[i]/p
        if rate==0:makes.append(0.);continue
        f=fam[0] if i<fam[0].stop else fam[1]; strength=200*la[i]/sum(la[f]); base=np.clip(lm[i]/la[i],EPS,1-EPS)
        ao=own[1+i];ad=allowed[1+i]; mo=own[1+n+i];md=allowed[1+n+i]
        ro=(ao+500*rate)/(own[0]+500);rd=(ad+500*rate)/(allowed[0]+500); rate_comb=np.clip(ro*rd/rate,0,1.5)
        po=np.clip((mo+strength*base)/(ao+strength),EPS,1-EPS);pd=np.clip((md+strength*base)/(ad+strength),EPS,1-EPS)
        accuracy=np.clip(expit(logit(po)+logit(pd)-logit(base)),EPS,1-EPS)
        makes.append(rate_comb*accuracy)
    rate=league[-2]/p;base=np.clip(league[-1]/league[-2],EPS,1-EPS)
    ro=(own[-2]+500*rate)/(own[0]+500);rd=(allowed[-2]+500*rate)/(allowed[0]+500);rate_comb=np.clip(ro*rd/rate,0,1.5)
    po=np.clip((own[-1]+200*base)/(own[-2]+200),EPS,1-EPS);pd=np.clip((allowed[-1]+200*base)/(allowed[-2]+200),EPS,1-EPS)
    free=rate_comb*np.clip(expit(logit(po)+logit(pd)-logit(base)),EPS,1-EPS)
    return np.array([sum(makes[fam[0]]),sum(makes[fam[1]]),free])

def probability(means):
    distributions=[]
    for side in means:
        d=np.array([1.])
        for mu,weight in zip(side,[2,3,1]):
            if not np.isfinite(mu) or mu<0 or poisson.sf(250,mu)>1e-12:raise ValueError('poisson_tail_or_mean')
            mass=np.zeros(250*weight+1);mass[::weight]=poisson.pmf(np.arange(251),mu);d=np.convolve(d,mass)
        distributions.append(d)
    h,a=distributions
    return float(np.dot(h,np.cumsum(a)-.5*a))

def fit_beta(rows):
    if not rows:raise ValueError('no_training_rows')
    x=np.array([r['feature_delta'] for r in rows]); y=np.array([r['home_win'] for r in rows]);q=np.clip([r['market'] for r in rows],EPS,1-EPS); offset=logit(q)
    def loss(beta):
        z=offset+beta*x
        return float(np.logaddexp(0,z).sum()-np.dot(y,z)+5*beta**2)
    res=minimize_scalar(loss,method='bounded',bounds=(-10,10),options={'xatol':1e-12})
    if not res.success:raise ValueError('fit failed')
    return {'beta':float(res.x),'n':len(rows),'penalty_l2':10}

def calc(rows,key):
    if not rows:return None
    y=np.array([r['home_win'] for r in rows]);prob=np.clip(np.array([r['probabilities'][key] for r in rows]),EPS,1-EPS);side=prob<.5;win=side==(1-y);odds=np.array([r['odds'][int(s)] for r,s in zip(rows,side)]);profit=win*odds-1
    daily=defaultdict(float)
    for r,v in zip(rows,profit):daily[r['day']]+=v
    c=np.cumsum([daily[d] for d in sorted(daily)]);peak=np.maximum.accumulate(np.r_[0,c])[1:]
    return {'n':len(rows),'wins':int(sum(win)),'hit_rate':float(np.mean(win)),'roi':float(np.mean(profit)),'log_loss':float(-np.mean(y*np.log(prob)+(1-y)*np.log(1-prob))),'brier':float(np.mean((prob-y)**2)),'mean_odds':float(np.mean(odds)),'daily_mdd':float(np.max(peak-c))}

def bootstrap(rows,model,ref):
    # Sufficient daily sums give the same paired date bootstrap without repeated object expansion.
    days=sorted({r['day'] for r in rows}); ix={d:i for i,d in enumerate(days)}; sums=np.zeros((len(days),5))
    for r in rows:
        y=r['home_win']; a=np.clip(r['probabilities'][model],EPS,1-EPS);b=np.clip(r['probabilities'][ref],EPS,1-EPS)
        sa=int(a<.5);sb=int(b<.5);wa=int(sa==1-y);wb=int(sb==1-y)
        sums[ix[r['day']]] += [1,wa-wb,wa*r['odds'][sa]-wb*r['odds'][sb],-(y*np.log(a/b)+(1-y)*np.log((1-a)/(1-b))),(a-y)**2-(b-y)**2]
    rng=np.random.default_rng(20261108); choices=rng.integers(0,len(days),(2000,len(days))); sampled=sums[choices].sum(axis=1); dif=sampled[:,1:]/sampled[:,[0]]
    return {k:np.percentile(dif[:,i],[2.5,97.5]).tolist() for i,k in enumerate(('hit_rate','roi','log_loss','brier'))}
def window(source,day):
    d=date.fromisoformat(day);end=d-timedelta(days=4);start=d-timedelta(days=365)
    return [g for g in source if start<=date.fromisoformat(g['day'])<=end]

def feature(game,source):
    active=window(source,game['day']); histories=[[g for g in active if t in g['teams']] for t in game['teams']]
    if any(len(h)<5 for h in histories):return None,'insufficient_team_history'
    league=np.sum([arr(s) for g in active for s in g['sides']],axis=0)
    own=[]; against=[]; possessions=[]
    for t,h in zip(game['teams'],histories):
        osides=[];dsides=[]
        for g in h:
            i=g['teams'].index(t);osides.append(arr(g['sides'][i]));dsides.append(arr(g['sides'][1-i]))
        o=np.sum(osides,axis=0);d=np.sum(dsides,axis=0);own.append(o);against.append(d);possessions.append((o[0]+d[0])/(2*len(h)))
    P=math.sqrt(possessions[0]*possessions[1]);means={}
    try:
        for key,spatial in (('nonspatial',False),('spatial',True)):
            means[key]=[P*aggregate(own[i],against[1-i],league,spatial) for i in range(2)]
    except ValueError as e:return None,str(e)
    result={}
    for key,v in means.items():
        result[key]=probability(v);result[key+'_expected_points']=[float(np.dot(x,[2,3,1])) for x in v]
    result['feature_delta']=float(logit(np.clip(result['spatial'],EPS,1-EPS))-logit(np.clip(result['nonspatial'],EPS,1-EPS)))
    result['history_game_ids']=[[g['game_id'] for g in h] for h in histories]
    result['asof_prior_games']=len(active)
    return result,None

def validate_pool(pool,manifest,byid):
    if len(pool)!=manifest['source_games'] or len({g['game_id'] for g in pool})!=len(pool):raise ValueError('source pool size/identity')
    for g in pool:
        source=byid.get(g['game_id'])
        if source is None or g['teams']!=source['teams'] or g['day']!=source['day']:raise ValueError('source identity mismatch '+g['game_id'])
        for i,s in enumerate(g['sides']):
            a=np.array(s['attempts']);m=np.array(s['makes']); box=source['stats'][i]
            if len(a)!=9 or len(m)!=9 or np.any(m>a) or np.any(m<0) or np.any(a<0) or sum(a)!=box.get('fga') or sum(m)!=box.get('fgm') or sum(m[5:])!=box.get('three') or s['fta']!=box.get('fta') or s['orb']!=box.get('orb') or s['tov']!=box.get('tov') or s['points']!=source['scores'][i] or s['points']!=2*sum(m[:5])+3*sum(m[5:])+s['ftm'] or abs(s['estimated_possessions']-(sum(a)-s['orb']+s['tov']+.44*s['fta']))>1e-8:
                raise ValueError('source box mismatch '+g['game_id'])

def main():
    manifest=json.loads((POOL/'manifest.json').read_text(encoding='utf8')); raw=(POOL/'games.jsonl.gz').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['games_sha256']:raise ValueError('pool hash mismatch')
    pool=[json.loads(s) for s in gzip.decompress(raw).splitlines()];pool.sort(key=lambda g:(g['day'],g['date_utc'],g['game_id']))
    events,dup,conf=old.read_events(CACHE);games=[g for e in events.values() if (g:=old.parse_game(e,CACHE))];byid={g['id']:g for g in games};validate_pool(pool,manifest,byid)
    bykey=defaultdict(list)
    for g in games:bykey[(g['day'],*g['teams'])].append(g)
    prices=[]; seen=set(); excluded=Counter(); archive_months=Counter()
    with gzip.open(PRICE,'rt',encoding='utf8') as f:
        for line in f:
            r=json.loads(line)
            if r['league']!='NBA' or not '2025-01-01'<=r['day']<='2026-10-27':continue
            archive_months[r['day'][:7]]+=1
            if r['id'] in seen:raise ValueError('duplicate price ID')
            seen.add(r['id']); key=(r['day'],old.name(r['home']),old.name(r['away']));matches=bykey[key]
            if len(matches)!=1:excluded['price_unmatched_or_ambiguous']+=1;continue
            g=matches[0]
            if (g['scores'][0]>g['scores'][1])!=(r['y']==0):raise ValueError('price result mismatch')
            q=1/r['odds'][0]/(1/r['odds'][0]+1/r['odds'][1]);prices.append({'id':r['id'],'espn_id':g['id'],'day':r['day'],'teams':g['teams'],'scores':g['scores'],'odds':r['odds'],'home_win':int(r['y']==0),'market':q})
    if len({r['espn_id'] for r in prices})!=len(prices):raise ValueError('duplicate fixture price')
    prior=[json.loads(s) for s in gzip.open(PREV/'predictions.jsonl.gz','rt',encoding='utf8')];pmap={r['id']:r for r in prices};targets=[]
    for r in prior:
        p=pmap.get(r['id'])
        if p is None or [p[k] for k in ('espn_id','day','teams','scores','odds','home_win')]!=[r[k] for k in ('espn_id','day','teams','scores','odds','home_win')] or abs(p['market']-r['market_home'])>1e-12:raise ValueError('prior baseline mismatch')
        targets.append(r)
    needed={r['id'] for r in targets} | {r['id'] for r in prices if r['day']<='2025-12-28'}
    features={}
    for r in prices:
        if r['id'] in needed:
            f,reason=feature(r,pool)
            if f is None:excluded['feature_'+reason]+=1
            else:features[r['id']]=f
    states={}; training_records=[]
    for name,cutoff in [('A','2025-08-28'),('B','2025-12-28')]:
        train=[dict(r,**features[r['id']]) for r in prices if r['day']<=cutoff and r['id'] in features]
        for tr in train:
            training_records.append({k:tr[k] for k in ('id','espn_id','day','teams','odds','home_win','market','history_game_ids','feature_delta','nonspatial','spatial','asof_prior_games')} | {'beta_state_id':name})
        states[name]=dict(fit_beta(train),train_ids_sha256=hashlib.sha256(('\n'.join(tr['id'] for tr in train)+'\n').encode()).hexdigest(),train_from='2025-01-01',train_through=cutoff,feature_rule='each row D-365 through D-4 inclusive')
    target_ids={g['game_id'] for g in pool}; rows=[];target_exclusions=Counter()
    for r in targets:
        f=features.get(r['id'])
        if f is None:target_exclusions['insufficient_history_or_prior']+=1;continue
        period='A' if r['day']<'2026-01-01' else 'B';beta=states[period]['beta']; p=expit(logit(np.clip(r['market_home'],EPS,1-EPS))+beta*f['feature_delta'])
        probs={'market':r['market_home'],'prior_outcome_form':r['existing_outcome_form'],'nonspatial':f['nonspatial'],'spatial':f['spatial'],'spatial_residual':float(p)}
        row={k:r[k] for k in ('id','espn_id','day','teams','scores','odds','home_win')};row.update({k:f[k] for k in ('history_game_ids','feature_delta','spatial_expected_points','nonspatial_expected_points','asof_prior_games')});row.update(period=period,beta_state_id=period,probabilities=probs,posthoc_target_in_source_pool=r['espn_id'] in target_ids)
        row['selections']={k:{'side':'home' if p>=.5 else 'away','odds':r['odds'][0 if p>=.5 else 1]} for k,p in probs.items()};rows.append(row)
    models=['market','prior_outcome_form','nonspatial','spatial','spatial_residual'];results={}
    for period in ('A','B'):
        subset=[r for r in rows if r['period']==period]; eligible=sum((r['day']<'2026-01-01')==(period=='A') for r in targets)
        months=sorted(m for m in archive_months if ('2025-09'<=m<='2025-12' if period=='A' else '2026-01'<=m<='2026-10'))
        monthly_coverage={m:{'archive_prices':archive_months[m],'baseline_common':sum(r['day'][:7]==m for r in targets),'evaluated':sum(r['day'][:7]==m for r in subset)} for m in months}
        result={'monthly_coverage':monthly_coverage,'actual_dates':[min(r['day'] for r in subset),max(r['day'] for r in subset)],'coverage':{'price_archive_rows':sum(archive_months[m] for m in months),'prior_rows':eligible,'evaluated':len(subset),'posthoc_source_target_subset':sum(r['posthoc_target_in_source_pool'] for r in subset)},'models':{k:calc(subset,k) for k in models},'monthly':{},'comparisons':{},'posthoc_target_source_subset':{}}
        for month in sorted({r['day'][:7] for r in subset}):
            subset_month=[r for r in subset if r['day'].startswith(month)];result['monthly'][month]={k:calc(subset_month,k) for k in models}
        refs=[('spatial','nonspatial')]+[(a,b) for a in ('nonspatial','spatial','spatial_residual') for b in ('market','prior_outcome_form')]
        for a,b in refs:result['comparisons'][a+'_minus_'+b]={'changed_picks':sum((r['probabilities'][a]>=.5)!=(r['probabilities'][b]>=.5) for r in subset),'paired_date_bootstrap_95':bootstrap(subset,a,b)}
        post=[r for r in subset if r['posthoc_target_in_source_pool']];result['posthoc_target_source_subset']={k:calc(post,k) for k in models};results[period]=result
    OUT.mkdir(parents=True,exist_ok=True);payload=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows).encode();(OUT/'predictions.jsonl.gz').write_bytes(gzip.compress(payload,mtime=0))
    (OUT/'results.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    training_payload=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in training_records).encode('utf8')
    (OUT/'train_features.jsonl.gz').write_bytes(gzip.compress(training_payload,mtime=0))
    meta={'protocol':'PROTOCOL.md','pool_sha256':hashlib.sha256(raw).hexdigest(),'pool_manifest':manifest,'previous_predictions_sha256':hashlib.sha256((PREV/'predictions.jsonl.gz').read_bytes()).hexdigest(),'price_sha256':hashlib.sha256(PRICE.read_bytes()).hexdigest(),'states':states,'source_scoreboard_duplicates':dup,'source_conflicts':conf,'all_price_exclusions':dict(excluded),'target_exclusions':dict(target_exclusions),'per_team_history_counts':{t:sum(t in g['teams'] for g in pool) for t in sorted({t for g in pool for t in g['teams']})},'evaluation_history_min_max':[min(len(h) for r in rows for h in r['history_game_ids']),max(len(h) for r in rows for h in r['history_game_ids'])]}
    (OUT/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf8');print(json.dumps({'coverage':{k:v['coverage'] for k,v in results.items()},'states':states},indent=2))
if __name__=='__main__':main()
