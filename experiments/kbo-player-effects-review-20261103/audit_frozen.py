"""Reconstruct frozen player-effect predictions and development selection.

Only immutable Git blobs are read. No 2026 labels or outcome ledgers are read.
"""
import copy,gzip,hashlib,json,math,re,subprocess
from collections import Counter
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix,hstack
from scipy.special import logsumexp
import audit_math as ref

WORKER=Path('C:/Users/user/Documents/Codex/kbeffects');ROOT=Path('experiments/kbo-player-effects-20261103');COMMIT='433d915aa52727de4af128cd9e614ca80aa8aa72';SEL='5dbddb0ea645ebf025dc5b7ce4c94ddca96ce35c';DATA=Path('C:/Users/user/Documents/Codex/kbodata');MODEL=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/kbo-player-model-20261103')
def git(repo,commit,path):return subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=repo)
def get(path,want=None):
    b=git(WORKER,COMMIT,ROOT/path)
    if want and hashlib.sha256(b).hexdigest()!=want:raise ValueError('pinned hash mismatch '+path)
    return b
def dec(raw):return [json.loads(x) for x in gzip.decompress(raw).splitlines()]
def index(rows):
    d={r['fixture_id']:r for r in rows}
    if len(d)!=len(rows):raise ValueError('duplicate fixture')
    return d
def scalar(k,v):
    def nat(x):return type(x) is int and x>=0
    if v is None or k=='park_factor':return None
    if k.endswith('_rest_days'):return float(v) if nat(v) else None
    if k.endswith('_team_strength'):
        x=[r['runs_for']-r['runs_against'] for r in v if nat(r.get('runs_for')) and nat(r.get('runs_against'))];return sum(x)/len(x) if x else None
    if k.endswith('_lineup_strength'):
        x=[z for p in v for z in p.get('history') or [] if all(nat(z.get(f)) for f in ('ab','hit','bb')) and z['hit']<=z['ab']];d=sum(z['ab']+z['bb'] for z in x)
        return sum(z['hit']+z['bb'] for z in x)/d if d else None
    if k.endswith('_starter_strength'):
        x=[z for z in v['appearances'] if nat(z.get('er')) and nat(z.get('outs'))];d=sum(z['outs'] for z in x)
        return 27*sum(z['er'] for z in x)/d if d else None
    if k.endswith('_bullpen_load'):
        g=v['games'];return float(sum(x['relief']['outs'] for x in g)) if g and all(nat((x.get('relief') or {}).get('outs')) for x in g) else None
    raise ValueError(k)
def oriented(r,side):
    opp='away' if side=='home' else 'home';keys=[side+'_team_strength',opp+'_team_strength',side+'_rest_days',opp+'_rest_days','park_factor',side+'_lineup_strength',opp+'_starter_strength',opp+'_bullpen_load'];return [r['values'].get(k) for k in keys]
def roster(s,scenario):
    o={}
    for side in ('home','away'):
        if scenario=='official_batting_and_starter':
            line=s['official_lineup_context']['lineups'][side];assert [p['order'] for p in line]==list(range(1,10));bat=[p['player_id'] for p in line];pitch=s['official_schedule_pitcher_context']['pitchers'][side]['player_id']
        else:bat=s['projection'][side+'_lineup']['player_ids'];pitch=s['projection'][side+'_starter']['player_id']
        if bat is not None:assert len(bat)==len(set(bat))==9 and all(isinstance(p,str) and p for p in bat)
        o[side]={'hitters':bat,'starter':pitch}
    return o
def rowspec(train):
    raw=np.array([oriented(r,s) for r in train for s in ('home','away')],dtype=float);mask=np.isfinite(raw);n=mask.sum(axis=0);mean=np.divide(np.where(mask,raw,0).sum(axis=0),n,out=np.zeros(8),where=n>0);var=np.divide(np.where(mask,(raw-mean)**2,0).sum(axis=0),n,out=np.zeros(8),where=n>0);scale=np.sqrt(var);scale[scale<1e-12]=1
    return {'teams':sorted({r[s+'_id'] for r in train for s in ('home','away')}),'hitters':sorted({p for r in train for s in ('home','away') for p in r['roster'][s]['hitters'] or []}),'starters':sorted({r['roster'][s]['starter'] for r in train for s in ('home','away') if r['roster'][s]['starter'] is not None}),'mean':mean.tolist(),'scale':scale.tolist()}
def checkspec(actual,expected):
    for k in ('teams','hitters','starters'):assert actual[k]==expected[k],k
    for k in ('mean','scale'):assert np.allclose(actual[k],expected[k],rtol=0,atol=1e-12),k
def vector(r,side,spec):
    vals=oriented(r,side);out=[];missing=[]
    for value,mean,scale in zip(vals,spec['mean'],spec['scale']):
        a,b=ref.numeric(value,mean,scale);out.append(a);missing.append(float(b))
    return np.array(out),np.array(missing)
def coverage(r,side,spec):
    opp='away' if side=='home' else 'home';x=ref.exposures(r['roster'][side]['hitters'],r['roster'][opp]['starter'],set(spec['hitters']),set(spec['starters']))
    return [float(x['lineup_missing']),x['unknown_hitter_count']/9,float(x['starter_missing']),float(x['unknown_starter'])]
def design_one(r,spec,family):
    teams=spec['teams'];n=len(teams);h=spec['hitters'];s=spec['starters'];hidx={x:i for i,x in enumerate(h)};pidx={x:i for i,x in enumerate(s)}
    normals=[vector(r,x,spec) for x in ('home','away')];flags=[coverage(r,x,spec) for x in ('home','away')];vectors=[]
    if family in ('M0','M1'):
        for i,side in enumerate(('home','away')):
            other='away' if side=='home' else 'home';x=[1.,float(side=='home')]+[float(r[side+'_id']==t) for t in teams]+[float(r[other+'_id']==t) for t in teams]+list(normals[i][0])+list(normals[i][1])
            if family=='M1':
                x+=flags[i];players=[0.]*(len(h)+len(s))
                for pid,w in zip(r['roster'][side]['hitters'] or [],ref.WEIGHTS):
                    if pid in hidx:players[hidx[pid]]+=w
                pid=r['roster'][other]['starter']
                if pid in pidx:players[len(h)+pidx[pid]]+=1
                x+=players
            vectors.append(x)
    else:
        x=[1.]+[float(r['home_id']==t)-float(r['away_id']==t) for t in teams]+list(normals[0][0]-normals[1][0])+list(normals[0][1]-normals[1][1])+list(np.array(flags[0])-flags[1]);players=[0.]*(len(h)+len(s))
        for side,sign in [('home',1),('away',-1)]:
            for pid,w in zip(r['roster'][side]['hitters'] or [],ref.WEIGHTS):
                if pid in hidx:players[hidx[pid]]+=sign*w
            pid=r['roster'][side]['starter']
            if pid in pidx:players[len(h)+pidx[pid]]+=sign
        vectors=[x+players]
    return np.asarray(vectors,dtype=float),flags
def predict(m,r):
    X,flags=design_one(r,m['spec'],m['family']);b=np.asarray(m['beta']);d=X.shape[1]
    if m['family']=='M2':
        z=X@b.reshape(2,d).T;p=ref.softmax([*z[0],0.]);rates=None
    else:
        z=X@b;rates=np.exp(z);p,_=ref.poisson_3way(*rates)
    return p,rates,flags
def fit_independently(rows,labels,family,penalty):
    """Refit only the registered inner 2023 candidates, never touch 2026 labels."""
    spec=rowspec(rows);parts=[design_one(r,spec,family)[0] for r in rows];X=csr_matrix(np.concatenate(parts));nbase=(2+2*len(spec['teams'])+16+4) if family=='M1' else (1+len(spec['teams'])+16+4);d=X.shape[1]
    y=[]
    for r in rows:
        q=labels[r['fixture_id']];a,b=q['home_score'],q['away_score'];y.extend([a,b] if family=='M1' else [0 if a>b else 1 if a==b else 2])
    y=np.asarray(y,dtype=int);reg=np.r_[np.array([0]+[10]*(nbase-1)),np.full(d-nbase,penalty)];classes=2 if family=='M2' else 1
    def obj(flat):
        weights=flat.reshape(classes,d)
        if family=='M1':
            z=X@weights[0]
            if np.max(abs(z))>30:return 1e100,np.zeros_like(flat)
            rate=np.exp(z);loss=np.sum(rate-y*z);grad=np.asarray(X.T@(rate-y)).reshape(1,d)
        else:
            z=np.column_stack((X@weights[0],X@weights[1],np.zeros(len(y))));logz=logsumexp(z,axis=1);p=np.exp(z-logz[:,None]);loss=np.sum(logz-z[np.arange(len(y)),y]);grad=np.vstack([X.T@(p[:,k]-(y==k)) for k in (0,1)])
        return float(loss+.5*np.sum(weights*weights*reg)),(grad+weights*reg).ravel()
    init=np.zeros(classes*d)
    if family=='M1':init[0]=math.log(max(float(y.mean()),.1))
    result=minimize(obj,init,jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
    if not result.success:raise ValueError('inner optimization did not converge')
    return {'family':family,'spec':spec,'beta':result.x.tolist()},float(np.max(np.abs(result.jac)))
def score(model,rows,labels):
    loss=brier=0.;hits=0
    for r in rows:
        p,_,_=predict(model,r);y=labels[r['fixture_id']];t=0 if y['home_score']>y['away_score'] else 1 if y['home_score']==y['away_score'] else 2
        loss-=math.log(max(p[t],1e-15));brier+=sum((v-int(i==t))**2 for i,v in enumerate(p));hits+=int(max(range(3),key=lambda i:(p[i],-i))==t)
    return {'n':len(rows),'hits':hits,'accuracy':hits/len(rows),'logloss':loss/len(rows),'brier':brier/len(rows)}

def main():
    c=ref.contract();assert hashlib.sha256(get('contract.json')).hexdigest()==ref.CONTRACT_SHA
    manifest=json.loads(get('inputs/manifest.json'));assert manifest['contract_sha256']==ref.CONTRACT_SHA
    inputs={}
    for name,h in manifest['files'].items():
        path=name if name.startswith('original_') else 'inputs/'+name;b=get(path);assert hashlib.sha256(b).hexdigest()==h;inputs[name]=b
    assert hashlib.sha256(gzip.decompress(inputs['rows.jsonl.gz'])).hexdigest()==c['input_pins']['rows_sha256']
    assert git(DATA,'13291d23',Path('experiments/kbo-player-data-20261103/labels.jsonl')) and hashlib.sha256(git(DATA,'13291d23',Path('experiments/kbo-player-data-20261103/labels.jsonl'))).hexdigest()==c['input_pins']['labels_sha256']
    assert git(MODEL,'b160d3d1',Path('experiments/kbo-player-model-20261103/phase2/features.jsonl.gz'))==inputs['features.jsonl.gz']
    for n in ('projected','official_batting_and_starter'):assert git(DATA,'0d980b4',Path('experiments/kbo-player-data-20261103/fresh-history-2026-'+n+'.jsonl.gz'))==inputs['fresh-history-2026-'+n+'.jsonl.gz']
    base=index(dec(inputs['features.jsonl.gz']));prior=index(dec(inputs['prior-projected-features.jsonl.gz']));raw=index(dec(inputs['rows.jsonl.gz']));labels=index(dec(inputs['development-labels.jsonl.gz']))
    assert len(base)==2913 and set(base)==set(prior)==set(raw) and len(labels)==2187 and all(base[f]['season']<2026 for f in labels)
    rawlabels={}
    for line in git(DATA,'13291d23',Path('experiments/kbo-player-data-20261103/labels.jsonl')).splitlines():
        match=re.search(rb'"fixture_id"\s*:\s*("(?:\\.|[^"\\])*")',line)
        if not match:raise ValueError('label ID missing')
        fid=json.loads(match.group(1))
        if fid in labels:rawlabels[fid]=json.loads(line)
    for fid,y in labels.items():assert (y['home_score'],y['away_score'])==(rawlabels[fid]['home_score'],rawlabels[fid]['away_score'])
    dev=[]
    for fid,b in base.items():
        p=prior[fid];values={}
        for key,e in p['features'].items():
            value=scalar(key,e['value']);assert value==b['features'][key]['value'];values[key]=value
        if b['season']<2026:dev.append({'fixture_id':fid,'event_date':b['event_date'],'season':b['season'],'home_id':b['home_id'],'away_id':b['away_id'],'values':values,'roster':roster(p,'projected')})
    dev.sort(key=lambda r:(r['event_date'],r['fixture_id']));scenarios={}
    for name in ('projected','official_batting_and_starter'):
        scenarios[name]=[]
        for p in dec(inputs['fresh-history-2026-'+name+'.jsonl.gz']):
            fid=p['fixture_id'];b=base[fid];assert b['season']==2026
            values={k:scalar(k,e['value']) for k,e in p['features'].items()}
            scenarios[name].append({'fixture_id':fid,'event_date':b['event_date'],'season':2026,'home_id':b['home_id'],'away_id':b['away_id'],'values':values,'roster':roster(p,name)})
        scenarios[name].sort(key=lambda r:(r['event_date'],r['fixture_id']))
    assert len(scenarios['projected'])==len(scenarios['official_batting_and_starter'])==709 and [x['fixture_id'] for x in scenarios['projected']]==[x['fixture_id'] for x in scenarios['official_batting_and_starter']]
    selection=json.loads(get('artifacts/selection.json','27c995c0d66bbc9a1e9c1a741735540adb21355ba76fbfac4eb4eff8a650eebb'));assert git(WORKER,SEL,ROOT/'artifacts/selection.json')==get('artifacts/selection.json')
    final=json.loads(get('artifacts/final-models.json','61bbcc11459ad7308bd0c881ac16cd54f4c1b5ecabab7745bfc78e78ec1e17be'));pred=json.loads(get('artifacts/predictions.json','ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9'))
    assert pred['2026_labels_read'] is False and selection['outcomes_after_2025_read'] is False and final['selection_commit']==pred['selection_commit']==SEL
    val24=[r for r in dev if r['season']==2024];val25=[r for r in dev if r['season']==2025];first26=scenarios['projected'][0]['event_date']
    def train(years,first):return [r for r in dev if r['season'] in years and date.fromisoformat(r['event_date'])<=date.fromisoformat(first)-timedelta(days=7)]
    inner=train((2023,),val24[0]['event_date']);outer=train((2023,2024),val25[0]['event_date']);last=train((2023,2024,2025),first26)
    assert (len(inner),len(val24),len(outer),len(val25),len(last))==(723,728,1451,736,2187) and final['training_n']==2187
    inner_losses={}; inner_delta={}
    for name in ('M1','M2'):
        inner_losses[name]={}
        for penalty in (1,10,100):
            m,grad=fit_independently(inner,labels,name,penalty);metric=score(m,val24,labels);saved=selection['inner']['results'][name][str(penalty)]['metrics']
            inner_delta[name+':'+str(penalty)]={k:metric[k]-saved[k] for k in ('logloss','brier','accuracy','hits')}
            assert abs(metric['logloss']-saved['logloss'])<1e-3,(name,penalty,metric,saved)
            inner_losses[name][penalty]=metric['logloss']
        assert ref.select_penalty(inner_losses[name])==selection['selected_player_penalties'][name]==100
    outer_metrics={};max_error=0.
    for name in ('M0','M1','M2'):
        saved=selection['outer']['models'][name];m=saved['model'];checkspec(m['spec'],rowspec(outer));assert m['n_train']==len(outer) and m['player_l2']==(10 if name=='M0' else 100)
        metric=score(m,val25,labels);outer_metrics[name]=(metric['logloss'],metric['brier'])
        for k in ('logloss','brier','accuracy'):assert abs(metric[k]-saved['metrics'][k])<1e-11
        for r,x in zip(val25,saved['predictions']):
            p,_,flags=predict(m,r);assert x['fixture_id']==r['fixture_id']
            for a,b in zip(p,[x['probabilities_3way'][k] for k in ('home','draw','away')]):max_error=max(max_error,abs(a-b))
            assert x['roster_coverage']==dict(zip(('home','away'),flags))
        checkspec(final['models'][name]['spec'],rowspec(last));assert final['models'][name]['n_train']==len(last)
        for scenario,rs in scenarios.items():
            for r,x in zip(rs,pred['scenarios'][scenario][name]):
                assert x['fixture_id']==r['fixture_id'];p,rates,flags=predict(final['models'][name],r)
                assert x['roster_coverage']==dict(zip(('home','away'),flags))
                for a,b in zip(p,[x['probabilities_3way'][k] for k in ('home','draw','away')]):max_error=max(max_error,abs(a-b))
                if rates is not None:
                    for a,b in zip(rates,[x['home_rate'],x['away_rate']]):max_error=max(max_error,abs(a-b))
                den=p[0]+p[2];assert abs(p[0]/den-x['probabilities_2way']['home'])<1e-10 and abs(p[2]/den-x['probabilities_2way']['away'])<1e-10
    assert ref.select_family(outer_metrics)==selection['selected_family']==final['selected_family']==pred['selected_family']=='M2'
    for scenario,info in final['counterfactual'].items():
        row=next(x for x in scenarios[scenario] if x['fixture_id']==info['fixture_id']);h1,h2=info['home_first_hitter'];p1,p2=info['away_starter']
        assert row['roster']['home']['hitters'][0]==h1 and row['roster']['away']['starter']==p1 and h2 in final['models']['M1']['spec']['hitters'] and p2 in final['models']['M1']['spec']['starters']
        variants={'original':row,'replace_home_first_hitter':copy.deepcopy(row),'replace_away_starter':copy.deepcopy(row)}
        variants['replace_home_first_hitter']['roster']['home']['hitters'][0]=h2;variants['replace_away_starter']['roster']['away']['starter']=p2
        for name,m in final['models'].items():
            for label,variant in variants.items():
                actual,_,_=predict(m,variant);saved=info['probabilities'][name][label]
                assert all(abs(v-saved[k])<1e-10 for v,k in zip(actual,('home','draw','away')))
        assert info['probabilities']['M0']['original']==info['probabilities']['M0']['replace_home_first_hitter']==info['probabilities']['M0']['replace_away_starter']
        for name in ('M1','M2'):
            for label in ('replace_home_first_hitter','replace_away_starter'):
                assert sum(abs(info['probabilities'][name][label][k]-info['probabilities'][name]['original'][k]) for k in ('home','draw','away'))>1e-12
    assert max_error<1e-10
    cancellation={}
    for scenario,ps in pred['scenarios'].items():
        n=Counter()
        for x in ps['M2']:
            home=x['roster_coverage']['home'];away=x['roster_coverage']['away']
            if home[1]>0 and away[1]>0:
                n['both_unknown_hitter']+=1
                if home[1]==away[1]:n['unknown_hitter_flag_cancels']+=1
            if home[3] and away[3]:n['both_unknown_pitcher_flag_cancels']+=1
            if home[0] and away[0]:n['both_lineups_missing_flag_cancels']+=1
        cancellation[scenario]=dict(n)
    print(json.dumps({'status':'PASS_WITH_INDICATOR_LIMITATION','inner_train':len(inner),'inner_validation':len(val24),'inner_selected':selection['selected_player_penalties'],'inner_independent_minus_saved':inner_delta,'outer_train':len(outer),'outer_validation':len(val25),'outer_selected':selection['selected_family'],'final_train':len(last),'2026_predictions':709*2*3,'max_prediction_error':max_error,'indicator_cancellation':cancellation,'2026_labels_read':False},sort_keys=True))
if __name__=='__main__':main()
