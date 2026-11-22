"""Retrospective pre-registered shot family volume conservation study."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[name]='1'
import gzip,hashlib,json,math
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.special import expit,logit
import nba_player_prior_snapshot as prior

ROOT=Path(__file__).resolve().parents[1]
OLD=Path(os.environ.get('NBA_SHOT_PREVIOUS','C:/Users/user/Documents/Codex/nba-shot-profile-model-20261108/experiments/nba-shot-profile-model-20261108'))
HARD=Path(os.environ.get('NBA_SHOT_HARD','C:/Users/user/Documents/Codex/nba-shot-profile-data-20261108/experiments/nba-shot-profile-data-20261108/games.jsonl.gz'))
SOFT=Path(os.environ.get('NBA_SHOT_SOFT','C:/Users/user/Documents/Codex/nba-spatial-structure-diagnostics-20261108/experiments/nba-spatial-structure-diagnostics-20261108/soft_games.jsonl.gz'))
OUT=Path(os.environ.get('NBA_CONSERVED_OUT',str(ROOT/'experiments/nba-spatial-conserved-model-20261108')))
EPS=1e-6

def family_details(own,allowed,league):
    p=league[0]; attempts=league[1:10];made=league[10:19];out=[]
    for sl in (slice(0,5),slice(5,9)):
        family=attempts[sl].sum()
        if family<=0 or p<=0:raise ValueError('missing family prior')
        rate=family/p; ao=own[1:10][sl].sum();ad=allowed[1:10][sl].sum()
        R=float(np.clip(((ao+500*rate)/(own[0]+500))*((ad+500*rate)/(allowed[0]+500))/rate,0,1.5))
        zone_rates=[];accuracy=[]
        for i in range(sl.start,sl.stop):
            a=attempts[i]
            if a==0:zone_rates.append(0.);accuracy.append(0.);continue
            r=a/p;base=np.clip(made[i]/a,EPS,1-EPS);strength=200*a/family
            x=own[1+i];y=allowed[1+i];u=own[10+i];v=allowed[10+i]
            rr=np.clip(((x+500*r)/(own[0]+500))*((y+500*r)/(allowed[0]+500))/r,0,1.5)
            po=np.clip((u+strength*base)/(x+strength),EPS,1-EPS);pd=np.clip((v+strength*base)/(y+strength),EPS,1-EPS)
            acc=np.clip(expit(logit(po)+logit(pd)-logit(base)),EPS,1-EPS)
            zone_rates.append(rr);accuracy.append(acc)
        if sum(zone_rates)<=0:raise ValueError('zero spatial family rate')
        weights=np.asarray(zone_rates)/sum(zone_rates);out.append({'family_rate':R,'weights':weights,'accuracy':np.asarray(accuracy),'made_rate':float(R*np.dot(weights,accuracy))})
    return out

def candidate(game,source):
    active=prior.window(source,game['day']);hist=[[g for g in active if team in g['teams']] for team in game['teams']]
    if any(len(h)<5 for h in hist):raise ValueError('insufficient team history')
    league=np.sum([prior.arr(s) for g in active for s in g['sides']],axis=0);own=[];against=[];poss=[]
    for team,h in zip(game['teams'],hist):
        pairs=[(prior.arr(g['sides'][g['teams'].index(team)]),prior.arr(g['sides'][1-g['teams'].index(team)])) for g in h]
        a=np.sum([x[0] for x in pairs],axis=0);b=np.sum([x[1] for x in pairs],axis=0);own.append(a);against.append(b);poss.append((a[0]+b[0])/(2*len(h)))
    P=math.sqrt(poss[0]*poss[1]);means=[];detail=[]
    for i in range(2):
        d=family_details(own[i],against[1-i],league); ft=prior.aggregate(own[i],against[1-i],league,False)[2]
        means.append(P*np.array([d[0]['made_rate'],d[1]['made_rate'],ft]));detail.append([{'family_rate':x['family_rate'],'weights':x['weights'].tolist(),'accuracy':x['accuracy'].tolist()} for x in d])
    qmeans=[P*prior.aggregate(own[i],against[1-i],league,False) for i in range(2)]
    return {'probability':prior.probability(means),'q':prior.probability(qmeans),'expected_points':[float(np.dot(v,[2,3,1])) for v in means],'history_game_ids':[[g['game_id'] for g in h] for h in hist],'detail':detail,'source_games':len(active)}

def read_gzip(path):return [json.loads(s) for s in gzip.decompress(path.read_bytes()).splitlines()]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save_gzip(path,rows):path.write_bytes(gzip.compress(''.join(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n' for r in rows).encode(),mtime=0))

def validate_pools(hard,soft):
    if len(hard)!=1185 or len(soft)!=1185:raise ValueError('source pool count')
    h={g['game_id']:g for g in hard};s={g['game_id']:g for g in soft}
    if len(h)!=len(hard) or len(s)!=len(soft) or h.keys()!=s.keys():raise ValueError('source IDs differ')
    for ident,a in h.items():
        b=s[ident]
        if a['day']!=b['day'] or a['teams']!=b['teams']:raise ValueError('source identity mismatch')
        for x,y in zip(a['sides'],b['sides']):
            for k in ('fta','ftm','orb','tov','estimated_possessions'):
                if not math.isclose(x[k],y[k],abs_tol=1e-8):raise ValueError('non-shot input changed')
            for k in ('attempts','makes'):
                for sl in (slice(0,5),slice(5,9)):
                    if not math.isclose(sum(x[k][sl]),sum(y[k][sl]),abs_tol=1e-7):raise ValueError('family shot totals changed')
    return sorted(hard,key=lambda g:(g['day'],g['date_utc'],g['game_id'])),sorted(soft,key=lambda g:(g['day'],g['date_utc'],g['game_id']))
def main():
    old_manifest=json.loads((OLD/'manifest.json').read_text(encoding='utf8'))
    if sha(HARD)!=old_manifest['pool_sha256']:raise ValueError('hard pool hash mismatch')
    diagnostics_path=SOFT.parent/'diagnostics.json'
    diagnostics=json.loads(diagnostics_path.read_text(encoding='utf8'))
    if sha(SOFT)!=diagnostics['soft_sha256'] or sha(HARD)!=diagnostics['input_sha256']['hard_games'] or sha(OLD/'predictions.jsonl.gz')!=diagnostics['input_sha256']['predictions'] or sha(OLD/'train_features.jsonl.gz')!=diagnostics['input_sha256']['train_features']:raise ValueError('diagnostic source hash mismatch')
    hard,soft=validate_pools(read_gzip(HARD),read_gzip(SOFT))
    train=read_gzip(OLD/'train_features.jsonl.gz');targets=read_gzip(OLD/'predictions.jsonl.gz')
    if len(targets)!=1260 or len({r['id'] for r in targets})!=1260:raise ValueError('target identity count')
    train_sets={p:[r for r in train if r['beta_state_id']==p] for p in 'AB'}
    for p,n in [('A',721),('B',1187)]:
        t=train_sets[p]
        if len(t)!=n or hashlib.sha256(('\n'.join(r['id'] for r in t)+'\n').encode()).hexdigest()!=old_manifest['states'][p]['train_ids_sha256']:raise ValueError('training identity mismatch')
    inputs={}
    for row in train+targets:
        if row['id'] in inputs:
            other=inputs[row['id']]
            if any(row[k]!=other[k] for k in ('espn_id','day','teams','history_game_ids')):raise ValueError('conflicting identity')
        else:inputs[row['id']]=row
    features={};reconstructed_max=0.
    for row in inputs.values():
        h=candidate(row,hard);f=candidate(row,soft)
        if h['history_game_ids']!=row['history_game_ids'] or f['history_game_ids']!=row['history_game_ids']:raise ValueError('source history mismatch')
        expected=row['nonspatial'] if 'nonspatial' in row else row['probabilities']['nonspatial']
        err=max(abs(h['q']-expected),abs(f['q']-expected));reconstructed_max=max(reconstructed_max,err)
        if err>1e-10:raise ValueError(f'nonspatial baseline changed {row["id"]}: {err}')
        features[row['id']]={'hard':h,'soft':f,'q':expected}
    states={};train_export=[]
    for period in 'AB':
        states[period]={}
        for kind in ('hard','soft'):
            beta_rows=[]
            for row in train_sets[period]:
                x=features[row['id']];delta=float(logit(np.clip(x[kind]['probability'],EPS,1-EPS))-logit(np.clip(x['q'],EPS,1-EPS)))
                beta_rows.append({'market':row['market'],'home_win':row['home_win'],'feature_delta':delta})
            states[period][kind]=dict(prior.fit_beta(beta_rows),train_from='2025-01-01',train_through='2025-08-28' if period=='A' else '2025-12-28',train_ids_sha256=old_manifest['states'][period]['train_ids_sha256'])
        for row in train_sets[period]:
            x=features[row['id']];q=x['q'];o={k:row[k] for k in ('id','espn_id','day','teams','odds','home_win','market','history_game_ids','beta_state_id')}
            o['probabilities']={'q':q,'hard':x['hard']['probability'],'soft':x['soft']['probability']};o['feature_delta']={k:float(logit(np.clip(x[k]['probability'],EPS,1-EPS))-logit(np.clip(q,EPS,1-EPS))) for k in ('hard','soft')};o['family_details']={k:x[k]['detail'] for k in ('hard','soft')};train_export.append(o)
    rows=[]
    for row in targets:
        x=features[row['id']]; period=row['period'];probs={'market':row['probabilities']['market'],'prior_outcome_form':row['probabilities']['prior_outcome_form'],'q':row['probabilities']['nonspatial'],'old_s':row['probabilities']['spatial'],'old_residual':row['probabilities']['spatial_residual'],'hard':x['hard']['probability'],'soft':x['soft']['probability']};deltas={}
        for kind in ('hard','soft'):
            deltas[kind]=float(logit(np.clip(probs[kind],EPS,1-EPS))-logit(np.clip(probs['q'],EPS,1-EPS)))
            probs[kind+'_anchor']=float(expit(logit(np.clip(probs['market'],EPS,1-EPS))+states[period][kind]['beta']*deltas[kind]))
        out={k:row[k] for k in ('id','espn_id','day','period','home_win','odds','teams','scores','history_game_ids','posthoc_target_in_source_pool')};out.update(probabilities=probs,feature_delta=deltas,expected_points={k:x[k]['expected_points'] for k in ('hard','soft')},family_details={k:x[k]['detail'] for k in ('hard','soft')},beta_state_id=period,selections={k:{'side':'home' if v>=.5 else 'away','odds':row['odds'][0 if v>=.5 else 1]} for k,v in probs.items()});rows.append(out)
    models=['market','prior_outcome_form','q','old_s','old_residual','hard','soft','hard_anchor','soft_anchor'];candidates=['hard','soft','hard_anchor','soft_anchor'];refs=['market','prior_outcome_form','q','old_s','old_residual'];results={}
    prev_results=json.loads((OLD/'results.json').read_text(encoding='utf8'))
    for phase in 'AB':
        subset=[r for r in rows if r['period']==phase];data={'actual_dates':[min(r['day'] for r in subset),max(r['day'] for r in subset)],'coverage':prev_results[phase]['coverage'],'monthly_coverage':prev_results[phase]['monthly_coverage'],'models':{k:prior.calc(subset,k) for k in models},'monthly':{},'comparisons':{},'posthoc_source_target_subset':{}}
        for month in sorted({r['day'][:7] for r in subset}):data['monthly'][month]={k:prior.calc([r for r in subset if r['day'][:7]==month],k) for k in models}
        for a in candidates:
            for b in refs:
                av=data['models'][a];bv=data['models'][b];data['comparisons'][a+'_minus_'+b]={'changed_picks':sum((r['probabilities'][a]>=.5)!=(r['probabilities'][b]>=.5) for r in subset),'joint_hit_roi_improvement':av['hit_rate']>bv['hit_rate'] and av['roi']>bv['roi'],'paired_date_bootstrap_95':prior.bootstrap(subset,a,b)}
        chosen=[r for r in subset if r['posthoc_target_in_source_pool']];data['posthoc_source_target_subset']={k:prior.calc(chosen,k) for k in models};results[phase]=data
    OUT.mkdir(parents=True,exist_ok=True);save_gzip(OUT/'predictions.jsonl.gz',rows);save_gzip(OUT/'train_features.jsonl.gz',train_export)
    (OUT/'results.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    meta={'diagnostics_sha256':sha(diagnostics_path),'hard_source_sha256':sha(HARD),'soft_source_sha256':sha(SOFT),'prior_prediction_sha256':sha(OLD/'predictions.jsonl.gz'),'prior_train_sha256':sha(OLD/'train_features.jsonl.gz'),'prior_manifest_sha256':sha(OLD/'manifest.json'),'prior_script_commit':'f116f87e','states':states,'nonspatial_reconstruction_max_abs_error':reconstructed_max,'train_rows':len(train_export),'evaluation_rows':len(rows),'price_sha256':old_manifest['price_sha256'],'protocol_commit':'c854390e'}
    (OUT/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf8');print(json.dumps({'states':states,'max_q_error':reconstructed_max,'coverage':{p:results[p]['coverage'] for p in 'AB'}},indent=2))
if __name__=='__main__':main()
