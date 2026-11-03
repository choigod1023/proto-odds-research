"""Single prespecified HR/AB addition to M0; no evaluation outcomes or prices."""
import copy,gzip,hashlib,json,sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix,hstack
from scipy.stats import skellam
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
sys.path.insert(0,str(ROOT/'experiments/kbo-m0-win-head-20261103'))
import train as shared
core=shared.core

def observations(row,side):
    for player in row['raw_features'][side+'_lineup_strength']['value'] or []:
        for x in player.get('history') or []:
            ab,hr=x.get('ab'),x.get('hr')
            if type(ab)is int and type(hr)is int and 0<=hr<=ab:
                yield player['player_id'],x,ab,hr

def fit_prior(rows):
    seen={}
    for row in rows:
        for side in ('home','away'):
            for player,x,ab,hr in observations(row,side):
                key=(player,x['fixture_id'],x['date'])
                if key in seen and seen[key]!=(ab,hr):raise ValueError('conflicting same-observation power counts')
                seen[key]=(ab,hr)
    ab=sum(x[0] for x in seen.values());hr=sum(x[1] for x in seen.values())
    if ab<=0:raise ValueError('no fit-only power observations')
    return hr/ab,len(seen)

def power(row,side,prior):
    players=row['raw_features'][side+'_lineup_strength']['value']
    if not players:return np.nan
    numer=denom=0.;known=False
    for player in players:
        valid=[x for x in player.get('history') or [] if type(x.get('ab'))is int and type(x.get('hr'))is int and 0<=x['hr']<=x['ab']]
        numer+=sum(x['hr'] for x in valid)+20*prior;denom+=sum(x['ab'] for x in valid)+20;known|=any(x['ab']>0 for x in valid)
    return numer/denom if known else np.nan

def columns(rows,prior,mean=None,scale=None):
    values=np.array([power(r,s,prior) for r in rows for s in ('home','away')]);missing=~np.isfinite(values)
    if mean is None:
        if missing.all():raise ValueError('all power features missing in fit')
        mean=float(np.mean(values[~missing]));scale=float(np.std(values[~missing]));scale=scale if scale>1e-12 else 1.
    return np.column_stack(((np.where(missing,mean,values)-mean)/scale,missing.astype(float))),mean,scale

def fit(rows,labels,baseline):
    prior,n=fit_prior(rows);col,mean,scale=columns(rows,prior)
    base,_=core.legacy.design([core.normalize(r) for r in rows],baseline['spec'],'M0');X=hstack((base,csr_matrix(col)),format='csr');y=core.legacy.target(rows,labels,'M0')
    pen=np.full(X.shape[1],10.);pen[0]=0
    def objective(beta):
        z=X@beta
        if np.max(np.abs(z))>30:return 1e100,np.zeros_like(beta)
        mu=np.exp(z);return float(np.sum(mu-y*z)+.5*np.sum(pen*beta*beta)),np.asarray(X.T@(mu-y))+pen*beta
    sol=minimize(objective,np.r_[baseline['beta'],0.,0.],jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
    if not sol.success or not np.isfinite(sol.x).all():raise ValueError('power model optimization failed')
    return {'spec':baseline['spec'],'beta':sol.x.tolist(),'prior':prior,'prior_unique_observations':n,'mean':mean,'scale':scale,'n_train':len(rows),'l2':10,'iterations':int(sol.nit),'gradient_max':float(np.max(np.abs(sol.jac)))}

def predict(model,rows,baseline):
    col,_,_=columns(rows,model['prior'],model['mean'],model['scale']);base,_=core.legacy.design([core.normalize(r) for r in rows],model['spec'],'M0');X=hstack((base,csr_matrix(col)),format='csr');z=X@np.asarray(model['beta'])
    if not np.isfinite(z).all() or np.max(np.abs(z))>20:raise ValueError('unstable predicted power rates')
    rates=np.exp(z).reshape(-1,2);h,a=rates[:,0],rates[:,1];q=np.column_stack((skellam.sf(0,h,a),skellam.pmf(0,h,a),skellam.cdf(-1,h,a)));q/=q.sum(1)[:,None];out=[]
    for i,(r,original) in enumerate(zip(rows,baseline)):
        p=copy.deepcopy(original);den=q[i,0]+q[i,2];p['probabilities_3way']={k:float(q[i,j]) for j,k in enumerate(('home','draw','away'))};p['probabilities_2way']={'home':float(q[i,0]/den),'away':float(q[i,2]/den)};p['home_rate']=float(h[i]);p['away_rate']=float(a[i]);out.append(p)
    return out

def main():
    core.verify();training=core.rows('train-actual.jsonl.gz');targets={y:[r for r in training if r['season']==y] for y in (2024,2025)};targets[2026]=core.rows('test-actual.jsonl.gz')
    for year in (2024,2025,2026):
        rows=sorted(targets[year],key=lambda r:(r['event_date'],r['fixture_id']));old=ROOT/f'experiments/kbo-m0-win-head-20261103/artifacts/{year}';baseline_model=json.loads((old/'models.json').read_bytes())['baseline'];base=json.loads(gzip.decompress((old/'predictions.json.gz').read_bytes()))['baseline']
        fitrows=sorted([r for r in training if r['season']<year],key=lambda r:(r['event_date'],r['fixture_id']));labels=shared.read_labels(shared.OLD/'inputs/fit2023-labels.jsonl.gz')
        for y in range(2024,year):labels.update(shared.read_labels(shared.OLD/f'data/labels-{y}.jsonl.gz'))
        if set(labels)!=set(core.unique(fitrows)) or set(labels)&set(core.unique(rows)):raise ValueError('past fold mismatch')
        if max(r['event_date'] for r in fitrows)>str(__import__('datetime').date.fromisoformat(rows[0]['event_date'])-__import__('datetime').timedelta(days=7)):raise ValueError('embargo failed')
        model=fit(fitrows,labels,baseline_model);pred=predict(model,rows,base);folder=P/f'artifacts/{year}';modelsha=shared.write(folder/'model.json',model);predsha=shared.write(folder/'predictions.json.gz',{'baseline':base,'power_m0':pred});shared.write(folder/'manifest.json',{'n_train':len(fitrows),'n_target':len(rows),'model_sha256':modelsha,'predictions_sha256':predsha,'contract_sha256':hashlib.sha256((P/'contract.json').read_bytes()).hexdigest(),'prices_and_target_labels_loaded':False});print({'year':year,'n':len(rows),'power_coefficient':model['beta'][-2]})
if __name__=='__main__':main()
