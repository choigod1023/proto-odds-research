"""Local new-feature experiments, with primary tipster and auxiliary markets apart.

No production imports. Feature-only auxiliary training may transfer to primary
matched evaluation; market-offset transfer across proxy/Shin domains is disabled.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from datetime import timedelta,timezone
from decimal import Decimal,ROUND_DOWN,ROUND_CEILING
import gzip,hashlib,itertools,json,math
from pathlib import Path
from functools import lru_cache
import numpy as np
from scipy.optimize import minimize

from rd_asof_evidence import feature_gate,timestamp,journal_registry

KST=timezone(timedelta(hours=9))
BUDGET=12000
ALIASES={'home':'home','홈':'home','away':'away','원정':'away','draw':'draw','무':'draw','무승부':'draw'}
TEAM_ALIAS_FILE=Path(__file__).parents[1]/'experiments/pregame-feature-rd-20261030/inputs/team-aliases.json'


@lru_cache(maxsize=1)
def team_aliases():
    # Explicit source-name equivalences only. No score-derived team joins.
    return {league:{name.strip().casefold():canonical.strip().casefold()for name,canonical in aliases.items()}
            for league,aliases in json.loads(TEAM_ALIAS_FILE.read_text(encoding='utf-8')).items()}


def team_key(row,side):
    name=row[side].strip().casefold();aliases=team_aliases().get(row['league'],{})
    return row['league']+':'+aliases.get(name,name)


def physical_key(row):
    # Exact kickoff, not date alone; doubleheaders at different times stay apart.
    return (row['league'],timestamp(row['kickoff']).isoformat(),team_key(row,'home'),team_key(row,'away'))


def deduplicate_cases(cases):
    groups=defaultdict(list);audit=Counter();result=[]
    for r in cases:groups[(r['cohort'],physical_key(r))].append(r)
    for group in groups.values():
        unique={r['event_id']:r for r in reversed(group)}
        audit['duplicate_full_match_rosters_omitted']+=len(group)-len(unique)
        values=list(unique.values())
        if len(values)>1:
            official={r.get('official_game_id')for r in values}
            # Source-ID aliases without an agreed official game ID could be
            # postponed/doubleheader ambiguity: exclude all, never guess a join.
            if None in official or len(official)!=1:
                audit['ambiguous_physical_alias_rows_excluded']+=len(values);continue
            audit['verified_physical_alias_rows_omitted']+=len(values)-1
        result.append(values[0])
    return result,dict(audit)


def load(path):
    op=gzip.open if str(path).endswith('.gz') else open
    with op(path,'rt',encoding='utf-8') as f:return [json.loads(line) for line in f if line.strip()]


def save(path,value,lines=False):
    op=gzip.open if str(path).endswith('.gz') else open
    with op(path,'wt',encoding='utf-8',newline='\n') as f:
        if lines:
            for r in value:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
        else:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def canonical_roster(r,auxiliary=False):
    if r['league'] not in ('K리그1','K리그2','KBO','NPB','MLB','KBL','NBA'):return None
    if r['market'] not in ('승무패','승패','full_game_moneyline'):return None
    try:
        if timestamp(r['available_at'])>timestamp(r['kickoff'])-timedelta(minutes=30):return None
    except (KeyError,TypeError,ValueError):return None
    choices=r['choices'];labels=[ALIASES.get(c.get('selection') or c.get('label'))for c in choices]
    expected=['home','away'] if len(choices)==2 else ['home','draw','away']
    if set(labels)!=set(expected) or len(labels)!=len(set(labels)):return None
    order=[labels.index(s)for s in expected]
    probs=r['market_p'];prices=r['odds']
    if not all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1 for x in probs):return None
    if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>=1 for x in prices):return None
    if abs(sum(probs)-1)>.002:return None
    target=r.get('target');outcome=order.index(target) if target is not None else None
    p=[probs[i]/sum(probs)for i in order]
    external=r.get('external_p') if r.get('tipster') else None
    score=r.get('external_p') if not r.get('tipster') else None
    final=r.get('final_p') if not r.get('tipster') else None
    return dict(cohort=r['cohort'],event_id=r['event_id'],league=r['league'],home=r['home'],away=r['away'],
                kickoff=r['kickoff'],decision_at=(timestamp(r['kickoff'])-timedelta(minutes=30)).isoformat(),
                available_at=r['available_at'],day=timestamp(r['kickoff']).astimezone(KST).date().isoformat(),
                issue=r['issue'],game_no=r['game_no'],sides=expected,market_p=p,
                external_p=[external[i]for i in order] if external else None,
                saved_score_p=[score[i]for i in order] if score else None,
                saved_final_p=[final[i]for i in order] if final else None,
                odds=[prices[i]for i in order],outcome=outcome,void=bool(r.get('void')),
                auxiliary=auxiliary,product_id=r['product_id'],
                official_game_id=r.get('official_game_id'),
                price_evidence='retrospective_archive_proxy_not_service' if auxiliary else 'matched_saved_quote_not_execution_certified')


def prepare_cases(primary_paths,archive_path,needed_aux):
    cases=[];audit=Counter();source=[]
    for path in primary_paths:
        source.append(dict(path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest()))
        for r in load(path):
            # Claimed-time tipsters only; raw -24h hypothetical cohort is not
            # silently pooled with its more conservative counterpart.
            if r['cohort'].startswith('retrospective') and not r['cohort'].endswith('__claimed_time_only'):continue
            c=canonical_roster(r)
            if c:
                ext=c['external_p']
                if ext is not None and ext.count(max(ext))!=1:
                    audit['primary_vote_ties_excluded_from_common_operating_support']+=1
                    continue
                cases.append(c)
    if archive_path:
        source.append(dict(path=str(archive_path),sha256=hashlib.sha256(Path(archive_path).read_bytes()).hexdigest()))
        for f in load(archive_path):
            if (f['cohort'],f['event_id']) not in needed_aux:continue
            groups=defaultdict(list)
            for c in f['choices']:
                if c['market'] in ('승패','승무패'):
                    groups[(c['issue_year'],c['round'],int(c['game_no']),c['market'])].append(c)
            chosen=None
            for key,options in sorted(groups.items()):
                if len(options) not in (2,3):continue
                void=all(c.get('refund')for c in options)
                hits=[c.get('hit')for c in options]
                target=hits.index(True)if None not in hits and sum(hits)==1 and not void else None
                r=dict(cohort=f['cohort'],event_id=f['event_id'],league=f['league'],home=f['home'],away=f['away'],
                       kickoff=f['kickoff'],available_at=f['available_at'],market=key[3],issue=list(key[:2]),game_no=str(key[2]),
                       market_p=[c['scores']['market_proportional_archive_proxy']for c in options],
                       choices=options,odds=[c['odds']for c in options],target=target,void=void,
                       product_id=options[0]['market_id'],tipster=False)
                chosen=canonical_roster(r,True)
                if chosen:break
            if chosen:cases.append(chosen)
            else:audit['aux_no_canonical_full_moneyline']+=1
    # Multiple market/reissue rows never become multiple physical targets.
    unique,duplicates=deduplicate_cases(sorted(cases,key=lambda r:(r['issue'],int(r['game_no']),r['product_id'])))
    audit.update(duplicates)
    return unique,dict(audit),source


def feature_index(paths):
    index=defaultdict(list);sources=[]
    for name,path in paths:
        sources.append(dict(feature_set=name,path=str(path),sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest()))
        for row in load(path):index[(name,row['cohort'],row['event_id'],row['kickoff'])].append(row)
    return index,sources


def attach(cases,index,feature_set,view,registry):
    rows=[];audit=Counter()
    for case in cases:
        try:
            if timestamp(case['available_at'])>timestamp(case['decision_at']):
                audit['case_input_after_decision']+=1;continue
        except (KeyError,TypeError,ValueError):audit['missing_case_input_time']+=1;continue
        feature_rows=index.get((feature_set,case['cohort'],case['event_id'],case['kickoff']),[])
        if not feature_rows:audit['no_feature_row']+=1;continue
        values={};evidence={};conflicts=set()
        for feature_row in feature_rows:
            try:
                if timestamp(feature_row['decision_at'])>timestamp(case['decision_at']):
                    audit['feature_extraction_after_target_decision']+=1;continue
            except (ValueError,KeyError,TypeError):audit['missing_feature_decision']+=1;continue
            for name,value in feature_row['features'].items():
                meta=feature_row.get('feature_evidence',{}).get(name)
                ok,reason=feature_gate(value,meta,feature_row['decision_at'],view=view,fixture=case['event_id'],registry=registry)
                audit['field:'+reason]+=1
                if not ok:continue
                if name in values and values[name]!=value:conflicts.add(name)
                else:values[name]=value;evidence[name]=meta
        for name in conflicts:values.pop(name,None);evidence.pop(name,None);audit['conflicting_feature_value']+=1
        if not values:audit['no_admissible_features']+=1;continue
        r=dict(case,features=values,feature_evidence=evidence,feature_set=feature_set,evidence_view=view)
        rows.append(r)
    audit['accepted_events']=len(rows)
    return rows,dict(audit)


def preprocessing(rows):
    names=sorted({n for r in rows for n in r['features']})
    medians=[];scales=[]
    for name in names:
        values=[r['features'][name]for r in rows if name in r['features']]
        medians.append(float(np.median(values)));s=float(np.std(values));scales.append(s if s>1e-8 else 1.)
    return dict(names=names,medians=medians,scales=scales)


def matrix(rows,prep,intercept_only=False):
    if intercept_only:return np.ones((len(rows),1))
    values=[]
    for r in rows:
        x=[];missing=[]
        for name,median,scale in zip(prep['names'],prep['medians'],prep['scales']):
            absent=name not in r['features'];v=r['features'].get(name,median)
            x.append(float(np.clip((v-median)/scale,-5,5)));missing.append(float(absent))
        values.append([1.]+x+missing)
    return np.asarray(values,dtype=float)


def softmax(logits):
    z=logits-np.max(logits,axis=1,keepdims=True);exp=np.exp(z)
    return exp/np.sum(exp,axis=1,keepdims=True)


def objective(theta,X,y,offset,k,l2=10.):
    beta=theta.reshape(X.shape[1],k-1)
    logits=offset+np.column_stack((X@beta,np.zeros(len(X))))
    p=softmax(logits)
    n=len(X);loss=-np.log(np.maximum(p[np.arange(n),y],1e-12)).mean()+l2*np.dot(theta,theta)/(2*n)
    error=p[:,:-1].copy()
    for i,target in enumerate(y):
        if target<k-1:error[i,target]-=1
    gradient=(X.T@error+l2*beta)/n
    return float(loss),gradient.ravel()


def fit_model(train,*,offset=False,intercept=False):
    if len({physical_key(r)for r in train})<30:return None
    prep=preprocessing(train);X=matrix(train,prep,intercept)
    k=len(train[0]['sides']);y=np.asarray([r['outcome']for r in train],dtype=int)
    off=np.log(np.maximum(np.asarray([r['market_p']for r in train]),1e-8))if offset else np.zeros((len(train),k))
    result=minimize(objective,np.zeros(X.shape[1]*(k-1)),args=(X,y,off,k),jac=True,method='L-BFGS-B',
                    options=dict(maxiter=500,ftol=1e-11,gtol=1e-7))
    if not result.success:return dict(status='optimizer_failed',message=str(result.message))
    return dict(status='fitted',preprocessing=prep,beta=result.x.tolist(),k=k,offset=offset,
                intercept_only=intercept,train_events=len(train),objective=float(result.fun))


def predict(rows,model):
    X=matrix(rows,model['preprocessing'],model['intercept_only']);k=model['k']
    beta=np.asarray(model['beta']).reshape(X.shape[1],k-1)
    off=np.log(np.maximum(np.asarray([r['market_p']for r in rows]),1e-8))if model['offset']else np.zeros((len(rows),k))
    return softmax(off+np.column_stack((X@beta,np.zeros(len(rows))))).tolist()


def metrics(rows,probabilities):
    valid=[(r,p)for r,p in zip(rows,probabilities)if r['outcome'] is not None or r['void']]
    if not valid:return dict(n=0,roi_pct=None)
    hit=net=0;loss=brier=0.;graded=0;shares=Counter();daily=defaultdict(float)
    for r,p in valid:
        pick=max(range(len(p)),key=lambda i:p[i]);shares[r['sides'][pick]]+=1
        profit=0 if r['void']else BUDGET*(r['odds'][pick]-1)if pick==r['outcome']else -BUDGET
        net+=profit;daily[r['day']]+=profit
        if r['outcome']is not None and not r['void']:
            graded+=1;hit+=pick==r['outcome'];loss-=math.log(max(p[r['outcome']],1e-8))
            brier+=sum((v-(i==r['outcome']))**2 for i,v in enumerate(p))
    equity=peak=dd=0
    for day in sorted(daily):equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
    return dict(n=len(valid),graded=graded,accuracy_pct=100*hit/graded if graded else None,
                brier=brier/graded if graded else None,logloss=loss/graded if graded else None,
                roi_pct=100*net/(BUDGET*len(valid)),net_krw=round(net,2),daily_close_drawdown_krw=round(dd,2),pick_counts=dict(shares))


def eligible_single_metrics(rows,probabilities):
    """Fixed existing eligibility rule; variable coverage is reported explicitly."""
    selected=[];vectors=[]
    for r,p in zip(rows,probabilities):
        pick=max(range(len(p)),key=lambda i:p[i])
        if p[pick]>=.55 and p[pick]*r['odds'][pick]>1:
            selected.append(r);vectors.append(p)
    return dict(available=len(rows),selected=len(selected),**metrics(selected,vectors))


def paired_delta(rows,p,q):
    blocks=defaultdict(list)
    for r,a,b in zip(rows,p,q):
        if r['outcome']is None and not r['void']:continue
        ia=max(range(len(a)),key=lambda i:a[i]);ib=max(range(len(b)),key=lambda i:b[i])
        if r['void']:
            blocks[r['day']].append((0,0,0));continue
        pa=r['odds'][ia]if ia==r['outcome']else 0;pb=r['odds'][ib]if ib==r['outcome']else 0
        blocks[r['day']].append((int(ia==r['outcome'])-int(ib==r['outcome']),pa-pb,1))
    n=sum(map(len,blocks.values()))
    if not n:return dict(n=0)
    graded=sum(x[2]for v in blocks.values()for x in v)
    result=dict(n=n,graded=graded,dates=len(blocks),accuracy_delta_pp=100*sum(x[0]for v in blocks.values()for x in v)/graded if graded else None,
                roi_delta_pp=100*sum(x[1]for v in blocks.values()for x in v)/n,ci95=None)
    if len(blocks)>=10:
        import random
        rng=random.Random(20261030);a=[];b=[];values=list(blocks.values())
        for _ in range(1000):
            sampled=rng.choices(values,k=len(values));den=sum(map(len,sampled))
            grades=sum(x[2]for v in sampled for x in v)
            if grades:a.append(100*sum(x[0]for v in sampled for x in v)/grades)
            b.append(100*sum(x[1]for v in sampled for x in v)/den)
        a.sort();b.sort();result['ci95']=dict(accuracy_delta_pp=[a[int(.025*len(a))],a[min(len(a)-1,int(.975*len(a)))]] if a else None,roi_delta_pp=[b[24],b[974]])
    return result


def pair_settlement(legs):
    if any(r['outcome']is None and not r['void']for r,p,i in legs):return None
    odds=[1 if r['void']else r['odds'][i]for r,p,i in legs]
    price=math.prod(Decimal(str(x))for x in odds).quantize(Decimal('.01'),rounding=ROUND_DOWN).quantize(Decimal('.1'),rounding=ROUND_CEILING)
    win=all(r['void']or i==r['outcome']for r,p,i in legs)
    net=float(BUDGET*(price-1))if win else -BUDGET
    single=sum((0 if r['void']else BUDGET/2*(r['odds'][i]-1)if i==r['outcome']else -BUDGET/2)for r,p,i in legs)
    return dict(net_krw=net,component_single_net_krw=single,quoted_odds=float(price),hit=win)


def two_leg(rows,probabilities,allowed_picks=None):
    groups=defaultdict(list)
    for j,(r,p)in enumerate(zip(rows,probabilities)):
        try:
            if timestamp(r['available_at'])>timestamp(r['decision_at']):continue
        except (KeyError,TypeError,ValueError):continue
        groups[(r['day'],tuple(r['issue']))].append((r,p,None if allowed_picks is None else allowed_picks[j]))
    tickets=[];opportunities=0
    for (day,issue),group in sorted(groups.items()):
        # Earliest point at which two feature/price payloads can coexist. Source
        # max_fact_time/date lag is still an assumption in reconstructed views.
        for when in sorted({timestamp(r['decision_at'])for r,p,allowed in group}):
            eligible=[(r,p,allowed)for r,p,allowed in group if timestamp(r['decision_at'])<=when and timestamp(r['kickoff'])>=when+timedelta(minutes=30)]
            if len(eligible)<2:continue
            if not any(physical_key(a)!=physical_key(b) and len({team_key(a,'home'),team_key(a,'away'),team_key(b,'home'),team_key(b,'away')})==4
                       for (a,p,ai),(b,q,bi)in itertools.combinations(eligible,2)):continue
            opportunities+=1;candidates=[]
            for r,p,allowed in eligible:
                choices=[i for i,v in enumerate(p)if v>=.55 and v*r['odds'][i]>1 and (allowed is None or i==allowed)]
                if choices:
                    i=max(choices,key=lambda i:p[i]);candidates.append((r,p,i))
            best=None;score=-1
            for a,b in itertools.combinations(candidates,2):
                if physical_key(a[0])==physical_key(b[0]):continue
                teams=[team_key(x[0],side)for x in (a,b)for side in ('home','away')]
                if len(set(teams))!=4:continue
                value=a[1][a[2]]*b[1][b[2]]
                if value>score:score=value;best=(a,b)
            if best:
                settlement=pair_settlement(best)
                tickets.append(dict(day=day,issue=list(issue),placement=when.isoformat(),
                                    legs=[dict(event_id=r['event_id'],product_id=r['product_id'],side=r['sides'][i],p=p[i],odds=r['odds'][i])for r,p,i in best],settlement=settlement))
            break  # No-fill at the fixed first common checkpoint, not later optimization.
    settled=[t for t in tickets if t['settlement']is not None]
    return dict(opportunities=opportunities,tickets=len(tickets),settled=len(settled),
                roi_pct=100*sum(t['settlement']['net_krw']for t in settled)/(BUDGET*len(settled))if settled else None,
                component_single_roi_pct=100*sum(t['settlement']['component_single_net_krw']for t in settled)/(BUDGET*len(settled))if settled else None,
                net_krw=sum(t['settlement']['net_krw']for t in settled),details=tickets)


def run(cases,features,feature_set,view,registry):
    data,audit=attach(cases,features,feature_set,view,registry);results=[];fits=[];predictions=[]
    for cohort,league,k in sorted({(r['cohort'],r['league'],len(r['sides']))for r in data}):
        rows=sorted([r for r in data if (r['cohort'],r['league'],len(r['sides']))==(cohort,league,k)],key=lambda r:(r['kickoff'],r['event_id']))
        dates=sorted({r['day']for r in rows})
        if len(dates)<5:
            results.append(dict(cohort=cohort,league=league,feature_set=feature_set,evidence_view=view,status='insufficient_dates',events=len(rows)));continue
        for frac in ((.6,.2,.2),(.5,.2,.3)):
            a=max(1,int(len(dates)*frac[0]));b=max(a+1,int(len(dates)*(frac[0]+frac[1])))
            if b>=len(dates):continue
            train_days,val_days,test_days=dates[:a],dates[a:b],dates[b:]
            val_start=min(timestamp(r['decision_at'])for r in rows if r['day']in val_days)
            primary_train=[r for r in rows if r['day']in train_days and r['outcome']is not None and timestamp(r['kickoff'])<val_start-timedelta(hours=48)]
            train=list(primary_train);aux_count=0
            if not rows[0]['auxiliary']:
                # Conservative whole-day exclusion prevents physical-event aliases
                # in broad auxiliary history from duplicating any primary target.
                primary_dates=set(dates)
                aux=[r for r in data if r['auxiliary']and r['league']==league and len(r['sides'])==k
                     and r['day']not in primary_dates and r['outcome']is not None and timestamp(r['kickoff'])<val_start-timedelta(hours=48)]
                train.extend(aux);aux_count=len(aux)
            test=[r for r in rows if r['day']in test_days]
            split='-'.join(str(round(x*100))for x in frac)
            base=dict(cohort=cohort,league=league,feature_set=feature_set,evidence_view=view,split=split,
                      train_n=len(train),train_primary_n=len(primary_train),train_aux_n=aux_count,test_n=len(test),
                      test_start=test_days[0],test_end=test_days[-1],strict_asof_training_certified=False,
                      status='evaluated'if len(test)>=10 else 'insufficient_heldout_events_descriptive_only')
            probabilities={'market':[r['market_p']for r in test]}
            if all(r['external_p']is not None for r in test):
                probabilities['actual_market90_tipster10']=[[(.9*m+.1*v)for m,v in zip(r['market_p'],r['external_p'])]for r in test]
            if all(r.get('saved_score_p')is not None for r in test):
                probabilities['saved_score_reference']=[r['saved_score_p']for r in test]
            if all(r.get('saved_final_p')is not None for r in test):
                probabilities['saved_final_reference']=[r['saved_final_p']for r in test]
            for name,offset,intercept in [('intercept_control',False,True),('feature_only',False,False),('market_feature_residual',True,False)]:
                if offset and aux_count:continue  # Never transfer proxy-market calibration to Shin anchors.
                model=fit_model(train,offset=offset,intercept=intercept)
                fits.append(dict(**base,model=name,fit=model,train_ids=[r['product_id']for r in train],test_ids=[r['product_id']for r in test]))
                if model and model['status']=='fitted':
                    # A test row with no fitted feature names is not imputed into a
                    # fake available feature model; require declared common support.
                    if not intercept and not all(set(r['features'])&set(model['preprocessing']['names'])for r in test):continue
                    probabilities[name]=predict(test,model)
            if 'feature_only'in probabilities and 'actual_market90_tipster10'in probabilities:
                probabilities['actual91_anchor90_feature10']=[[(.9*a+.1*f)for a,f in zip(anchor,feature)]for anchor,feature in zip(probabilities['actual_market90_tipster10'],probabilities['feature_only'])]
            for name,p in probabilities.items():
                results.append(dict(**base,model=name,metrics=metrics(test,p),delta_vs_market=paired_delta(test,p,probabilities['market']),
                                    delta_vs_actual91=paired_delta(test,p,probabilities['actual_market90_tipster10'])if 'actual_market90_tipster10'in probabilities else None,
                                    eligible_single=eligible_single_metrics(test,p),two_leg=two_leg(test,p)))
                for r,pr in zip(test,p):predictions.append(dict(cohort=cohort,league=league,feature_set=feature_set,evidence_view=view,
                    split=split,model=name,event_id=r['event_id'],product_id=r['product_id'],kickoff=r['kickoff'],
                    probabilities=pr,outcome=r['outcome'],odds=r['odds'],source_evidence=r['feature_evidence']))
            directions={'home_always_direction_reference':[r['sides'].index('home')for r in test]}
            if all(r['external_p']is not None for r in test):
                directions['operating_unique_vote_market_reference']=[max(range(k),key=lambda i:r['external_p'][i])for r in test]
            for name,picks in directions.items():
                onehot=[[float(i==chosen)for i in range(k)]for chosen in picks]
                measured=metrics(test,onehot);measured['brier']=None;measured['logloss']=None
                results.append(dict(**base,model=name,probability_semantics='direction-only reference; no calibrated probability vector',
                                    metrics=measured,delta_vs_market=paired_delta(test,onehot,probabilities['market']),
                                    delta_vs_actual91=paired_delta(test,onehot,probabilities['actual_market90_tipster10'])if 'actual_market90_tipster10'in probabilities else None,
                                    two_leg=two_leg(test,probabilities['market'],picks)))
    return dict(coverage=audit,results=results,fits=fits,predictions=predictions)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--rosters',nargs='+',type=Path)
    source.add_argument('--cases',type=Path,help='Replay the frozen normalized cases from a previous run, without original worktrees')
    p.add_argument('--archive',type=Path)
    p.add_argument('--feature-file',action='append',required=True,help='FEATURE_SET=path.jsonl.gz')
    p.add_argument('--journal',action='append',default=[],type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    inputs=[(name,Path(path))for name,path in (value.split('=',1)for value in args.feature_file)]
    index,feature_sources=feature_index(inputs)
    aux={(cohort,event)for name,cohort,event,kickoff in index if cohort=='historical_market_proxy'}
    if args.cases:
        if args.archive:p.error('--archive is only used with --rosters')
        cases,duplicates=deduplicate_cases(load(args.cases));audit={'frozen_cases_replayed':len(cases),**duplicates}
        case_sources=[dict(path=str(args.cases),sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest())]
    else:cases,audit,case_sources=prepare_cases(args.rosters,args.archive,aux)
    registry=journal_registry(args.journal);all_results=[];all_fits=[];all_predictions=[];coverage=[]
    for name in sorted({n for n,path in inputs}):
        for view in ('captured_pregame','retrospective_lagged','retrospective_date_lagged'):
            result=run(cases,index,name,view,registry)
            coverage.append(dict(feature_set=name,evidence_view=view,**result['coverage']))
            all_results.extend(result['results']);all_fits.extend(result['fits']);all_predictions.extend(result['predictions'])
    save(args.output/'cases.jsonl.gz',cases,True);save(args.output/'coverage.json',coverage)
    save(args.output/'results.json',all_results);save(args.output/'fits.json',all_fits)
    save(args.output/'predictions.jsonl.gz',all_predictions,True)
    save(args.output/'manifest.json',dict(protocol='pregame-feature-rd-v1',production_changed=False,
        research_monthly_loss_cap=None,case_sources=case_sources,feature_sources=feature_sources,case_audit=audit,
        team_alias_source=dict(path=str(TEAM_ALIAS_FILE.relative_to(Path(__file__).parents[1])),sha256=hashlib.sha256(TEAM_ALIAS_FILE.read_bytes()).hexdigest()),
        cases=len(cases),result_rows=len(all_results),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    print(json.dumps(dict(cases=len(cases),result_rows=len(all_results),fitted_artifacts=sum(f['fit']is not None for f in all_fits))))


if __name__=='__main__':main()
