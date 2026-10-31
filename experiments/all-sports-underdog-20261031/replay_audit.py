"""Independent probability/feature/state replay; no common.py prediction import."""
from collections import defaultdict,deque
from datetime import date,timedelta
import hashlib,json,math
import numpy as np
from scipy.optimize import minimize


class AuditError(ValueError):pass


def require(ok,message):
    if not ok:raise AuditError(message)


def features(source):
    rows=sorted(source,key=lambda r:(r['day'],r['kickoff'],r['id']))
    require(len(rows)==len({r['id'] for r in rows}),'duplicate source identity')
    strength={};recent={};counts={};cursor=0;out=[]
    ks={'bs':12.,'sc':24.,'bk':16.,'vl':18.}
    for i,r in enumerate(rows):
        cutoff=(date.fromisoformat(r['day'])-timedelta(days=4)).isoformat()
        while cursor<i and rows[cursor]['day']<=cutoff:
            prior=rows[cursor];cursor+=1;s=prior['sport'];l=prior['league']
            h=(s,l,prior['home']);a=(s,l,prior['away'])
            rh=strength.get(h,1500.);ra=strength.get(a,1500.)
            expected=1/(1+math.exp(-math.log(10)*(rh+45-ra)/400))
            result=1. if prior['y']==0 else .5 if s=='sc' and prior['y']==1 else 0.
            step=ks[s]*(result-expected);strength[h]=rh+step;strength[a]=ra-step
            recent.setdefault(h,deque(maxlen=20)).append(result)
            recent.setdefault(a,deque(maxlen=20)).append(1-result)
            k=3 if s=='sc' else 2
            counts.setdefault((s,l),[1]*k)[prior['y']]+=1
        s=r['sport'];l=r['league'];h=(s,l,r['home']);a=(s,l,r['away']);k=len(r['odds'])
        totals=counts.get((s,l),[1]*k);total=sum(totals)
        fh=sum(recent[h])/len(recent[h]) if recent.get(h) else .5
        fa=sum(recent[a])/len(recent[a]) if recent.get(a) else .5
        out.append([(strength.get(h,1500.)+45-strength.get(a,1500.))/400,fh-fa,totals[0]/total,totals[1]/total if k==3 else 0.])
    return rows,np.array(out,dtype=float)


def probabilities(logits):
    values=logits-logits.max(axis=1,keepdims=True)
    weights=np.exp(values)
    return weights/weights.sum(axis=1,keepdims=True)


def loss_gradient(beta,design,labels,market,offset):
    logits=design@beta+(np.log(market) if offset else 0)
    maxima=logits.max(1)
    norm=maxima+np.log(np.exp(logits-maxima[:,None]).sum(1))
    penalty=beta.copy();penalty[0]=0
    loss=float(np.mean(norm-logits[np.arange(len(labels)),labels]))+.01*float((penalty**2).sum())
    actual=np.zeros_like(logits);actual[np.arange(len(labels)),labels]=1
    gradient=design.T@(probabilities(logits)-actual)/len(labels)+.02*penalty
    return loss,gradient


def bfgs_refit(design,labels,market,offset):
    """Different optimizer, centered parameterization; same frozen objective."""
    width=design.shape[1];k=market.shape[1]
    def unpack(v):
        b=v.reshape(width,k-1)
        return np.column_stack([b,-b.sum(1)])
    def objective(v):
        loss,g=loss_gradient(unpack(v),design,labels,market,offset)
        return loss,(g[:,:-1]-g[:,-1,None]).ravel()
    result=minimize(objective,np.zeros(width*(k-1)),jac=True,method='BFGS',options={'gtol':1e-8,'maxiter':1000})
    return unpack(result.x),float(np.max(np.abs(objective(result.x)[1])))


def check(source,ledger,states,refit=False):
    rows,x=features(source);byid={r['id']:r for r in ledger}
    require(set(byid)=={r['id'] for r in rows} and len(byid)==len(ledger),'source/ledger identity mismatch')
    require(len({r['sport'] for r in rows})==1,'mixed sports')
    labels=np.array([r['y'] for r in rows]);q=np.array([[1/float(o) for o in r['odds']] for r in rows]);q/=q.sum(1,keepdims=True)
    days=np.array([r['day'] for r in rows]);months=np.array([d[:7] for d in days])
    require(len(states)==len({s['month'] for s in states}),'duplicate monthly state')
    smap={s['month']:s for s in states}
    require(set(smap)==set(months),'monthly state coverage')
    modelnames={'market','outcome_form','market_residual','half_market_form'}
    maximum_p=maximum_mean=maximum_scale=maximum_gradient=0.;checked=0;refits=[]
    for month in sorted(smap):
        state=smap[month];cutoff=(date.fromisoformat(month+'-01')-timedelta(days=4)).isoformat()
        require(state['training_cutoff_inclusive']==cutoff,'cutoff differs from day+4 protocol')
        tr=np.where(days<=cutoff)[0];te=np.where(months==month)[0];k=q.shape[1]
        eligible=len(tr)>=200 and len(set(labels[tr]))==k
        require(state['training_rows']==len(tr),'training count mismatch')
        require(state['fallback']==(not eligible),'fallback eligibility mismatch')
        require(set(state['states'])==({'outcome_form','market_residual'} if eligible else set()),'model state coverage')
        digest=hashlib.sha256(json.dumps(state['states'],sort_keys=True).encode()).hexdigest()
        require(digest==state['state_sha256'],'state digest mismatch')
        pp={'market':q[te]}
        for name in ('outcome_form','market_residual'):
            if not eligible:pp[name]=q[te];continue
            s=state['states'][name];mean=x[tr].mean(0);scale=x[tr].std(0);scale=np.where(scale>1e-12,scale,1.)
            dm=float(np.max(np.abs(mean-s['mean'])));ds=float(np.max(np.abs(scale-s['scale'])))
            maximum_mean=max(maximum_mean,dm);maximum_scale=max(maximum_scale,ds)
            require(dm<1e-10 and ds<1e-10,'prior-only scaler mismatch')
            require(s['training_rows']==len(tr) and s['offset']==(name=='market_residual'),'model configuration mismatch')
            beta=np.asarray(s['beta']);require(beta.shape==(x.shape[1]+1,k) and np.isfinite(beta).all(),'coefficient shape/nonfinite')
            design=np.column_stack([np.ones(len(tr)),(x[tr]-mean)/scale])
            _,g=loss_gradient(beta,design,labels[tr],q[tr],s['offset']);grad=float(np.max(np.abs(g)))
            maximum_gradient=max(maximum_gradient,grad);require(grad<5e-5,'saved state is not stationary for prior-only objective')
            test=np.column_stack([np.ones(len(te)),(x[te]-mean)/scale])
            pp[name]=probabilities(test@beta+(np.log(q[te]) if s['offset'] else 0))
            if refit and not any(v['model']==name for v in refits):
                fitted,residual=bfgs_refit(design,labels[tr],q[tr],s['offset'])
                fitted_p=probabilities(test@fitted+(np.log(q[te]) if s['offset'] else 0))
                difference=float(np.max(np.abs(fitted_p-pp[name])))
                require(residual<1e-6 and difference<1e-5,'independent BFGS refit disagrees')
                refits.append(dict(model=name,month=month,training=len(tr),max_probability_difference=difference,max_gradient=residual))
        pp['half_market_form']=(pp['market']+pp['outcome_form'])/2
        for j,i in enumerate(te):
            row=byid[rows[i]['id']]
            require(row['fallback']==state['fallback'] and row['month_state_sha256']==digest,'row/state metadata mismatch')
            require(set(row['predictions'])==modelnames,'prediction model coverage')
            for name,p in pp.items():
                actual=np.array(row['predictions'][name]['p'])
                require(actual.shape==(k,) and np.isfinite(actual).all(),'probability vector shape/nonfinite')
                delta=float(np.max(np.abs(actual-p[j])))
                maximum_p=max(maximum_p,delta);require(delta<1e-10,'probability_state_mismatch: '+name)
                require(int(np.argmax(p[j]))==row['predictions'][name]['pick'],'replayed pick differs')
                checked+=1
    return dict(fixtures=len(rows),probabilities_replayed=checked,max_probability_difference=maximum_p,
                max_scaler_mean_difference=maximum_mean,max_scaler_scale_difference=maximum_scale,
                max_objective_gradient=maximum_gradient,independent_refits=refits)
