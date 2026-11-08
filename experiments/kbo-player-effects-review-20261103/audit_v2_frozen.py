"""Independent corrected M2 selection and frozen predictions; no 2026 scoring."""
import gzip,hashlib,json,math
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix
from scipy.special import logsumexp
import audit_math as common
import audit_frozen as original
import audit_v2_design as corrected

WORKER=original.WORKER;ROOT=original.ROOT;git=original.git;COMMIT='18c2e84d6962f9016b20996e543e5b4bb6c70975';SELECTION='d28b4101f81fea02d4046dea7d05c47449fffb55'
def read(path,expected=None):
    b=git(WORKER,COMMIT,ROOT/path)
    if expected:assert hashlib.sha256(b).hexdigest()==expected,path
    return b
def decode(name):return original.dec(git(WORKER,original.COMMIT,ROOT/('inputs/'+name)))
def predict(model,row):
    x,_,_=corrected.design(row,model['spec']);d=len(x);beta=np.asarray(model['beta']).reshape(2,d);logits=beta@x;p=common.softmax([*logits,0.]);return p
def evaluate(model,rows,labels):
    ll=br=0.;hits=0
    for r in rows:
        p=predict(model,r);label=labels[r['fixture_id']];y=0 if label['home_score']>label['away_score'] else 1 if label['home_score']==label['away_score'] else 2
        ll-=math.log(max(p[y],1e-15));br+=sum((v-int(i==y))**2 for i,v in enumerate(p));hits+=int(max(range(3),key=lambda k:(p[k],-k))==y)
    return {'n':len(rows),'hits':hits,'accuracy':hits/len(rows),'brier':br/len(rows),'logloss':ll/len(rows)}
def fit_inner(rows,labels,penalty):
    spec=original.rowspec(rows);matrix=csr_matrix(np.stack([corrected.design(r,spec)[0] for r in rows]));nbase=1+len(spec['teams'])+16+4+12;d=matrix.shape[1];reg=np.r_[np.array([0]+[10]*(nbase-1)),np.full(d-nbase,penalty)]
    y=[]
    for row in rows:
        q=labels[row['fixture_id']];a,b=q['home_score'],q['away_score'];y.append(0 if a>b else 1 if a==b else 2)
    y=np.asarray(y)
    def obj(flat):
        beta=flat.reshape(2,d);logits=np.column_stack((matrix@beta[0],matrix@beta[1],np.zeros(len(y))));logz=logsumexp(logits,axis=1);p=np.exp(logits-logz[:,None]);loss=np.sum(logz-logits[np.arange(len(y)),y]);grad=np.vstack([matrix.T@(p[:,k]-(y==k)) for k in (0,1)])
        return float(loss+.5*np.sum(beta*beta*reg)),(grad+beta*reg).ravel()
    sol=minimize(obj,np.zeros(2*d),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
    if not sol.success:raise ValueError('inner fit failed')
    return {'spec':spec,'beta':sol.x.tolist()}
def main():
    corrected.correction_contract();v1=json.loads(git(WORKER,original.SEL,ROOT/'artifacts/selection.json'))
    selected=json.loads(read('v2/selection.json','9fa9c300ce4e74c938eefcbdcb745a06fed3006f79fc59855f33fe557e0ff611'));assert git(WORKER,SELECTION,ROOT/'v2/selection.json')==read('v2/selection.json')
    saved=json.loads(read('v2/model.json','a4602fecc037cbe5bc480a48b0c0c20bba4f5a7dc12262aae313732169b3568f'));pred=json.loads(read('v2/predictions.json','6d3dd2cc15d4a4681335e1984c76e8526bf94aae9f46fe372d5b4c10b1abced5'))
    assert selected['retrospective_correction_after_v1_2026_seen'] is pred['not_blind_prior_2026_seen'] is True and pred['2026_labels_read'] is False and selected['outcomes_after_2025_read'] is False
    assert saved['selection_commit']==pred['selection_commit']==SELECTION and saved['original_M0_M1_unchanged'] is True
    for f in ('M0','M1'):assert selected['outer']['families'][f]['metrics']==v1['outer']['models'][f]['metrics']
    base=original.index(decode('features.jsonl.gz'));prior=original.index(decode('prior-projected-features.jsonl.gz'));labels=original.index(decode('development-labels.jsonl.gz'))
    dev=[]
    for fid,b in base.items():
        if b['season']>=2026:continue
        src=prior[fid];values={k:original.scalar(k,e['value']) for k,e in src['features'].items()};assert all(values[k]==b['features'][k]['value'] for k in values)
        dev.append({'fixture_id':fid,'event_date':b['event_date'],'season':b['season'],'home_id':b['home_id'],'away_id':b['away_id'],'values':values,'roster':original.roster(src,'projected')})
    dev.sort(key=lambda x:(x['event_date'],x['fixture_id']));v24=[x for x in dev if x['season']==2024];v25=[x for x in dev if x['season']==2025]
    scenarios={}
    for name in ('projected','official_batting_and_starter'):
        out=[]
        for src in decode('fresh-history-2026-'+name+'.jsonl.gz'):
            b=base[src['fixture_id']];out.append({'fixture_id':src['fixture_id'],'event_date':b['event_date'],'season':2026,'home_id':b['home_id'],'away_id':b['away_id'],'values':{k:original.scalar(k,e['value']) for k,e in src['features'].items()},'roster':original.roster(src,name)})
        scenarios[name]=sorted(out,key=lambda x:(x['event_date'],x['fixture_id']))
    def train(years,first):return [x for x in dev if x['season'] in years and date.fromisoformat(x['event_date'])<=date.fromisoformat(first)-timedelta(days=7)]
    tr23=train((2023,),v24[0]['event_date']);tr24=train((2023,2024),v25[0]['event_date']);tr25=train((2023,2024,2025),scenarios['projected'][0]['event_date']);assert (len(tr23),len(v24),len(tr24),len(v25),len(tr25))==(723,728,1451,736,2187)
    losses={};delta={}
    for alpha in (1,10,100):
        model=fit_inner(tr23,labels,alpha);m=evaluate(model,v24,labels);old=selected['inner']['results'][str(alpha)]['metrics'];losses[alpha]=m['logloss'];delta[str(alpha)]={k:m[k]-old[k] for k in ('logloss','brier','accuracy','hits')};assert abs(delta[str(alpha)]['logloss'])<1e-3
    assert common.select_penalty(losses)==selected['selected_player_penalty']==100
    m=selected['outer']['families']['M2']['model'];original.checkspec(m['spec'],original.rowspec(tr24));assert m['n_train']==1451 and m['quality_sum_columns']==12 and m['nbase']==1+len(m['spec']['teams'])+16+4+12
    metrics=evaluate(m,v25,labels);recorded=selected['outer']['families']['M2']['metrics']
    assert all(abs(metrics[k]-recorded[k])<1e-11 for k in ('accuracy','brier','logloss')) and metrics['hits']==recorded['hits']
    family={name:(v['metrics']['logloss'],v['metrics']['brier']) for name,v in selected['outer']['families'].items()};assert common.select_family(family)==selected['selected_family']==saved['selected_family']==pred['selected_family']=='M2'
    model=saved['model'];original.checkspec(model['spec'],original.rowspec(tr25));assert model['quality_sum_columns']==12 and model['player_l2']==100 and model['n_train']==2187
    error=0.
    for name,rows in scenarios.items():
        assert len(rows)==len(pred['scenarios'][name])==709
        for row,output in zip(rows,pred['scenarios'][name]):
            assert row['fixture_id']==output['fixture_id'];p=predict(model,row)
            for a,k in zip(p,('home','draw','away')):error=max(error,abs(a-output['probabilities_3way'][k]))
            den=p[0]+p[2];assert abs(p[0]/den-output['probabilities_2way']['home'])<1e-12 and abs(p[2]/den-output['probabilities_2way']['away'])<1e-12
            assert output['roster_coverage']=={side:original.coverage(row,side,model['spec']) for side in ('home','away')}
    assert error<1e-11
    print(json.dumps({'status':'PASS','inner_train':723,'inner_validation':728,'inner_selected':100,'inner_recomputed_minus_saved':delta,'outer_train':1451,'outer_validation':736,'selected_family':'M2','final_train':2187,'added_quality_columns':12,'frozen_predictions':1418,'max_probability_error':error,'2026_outcome_used':False,'not_blind':True},sort_keys=True))
if __name__=='__main__':main()
