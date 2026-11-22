"""Secondary exploratory prediction of winner counts, NOT winning numbers."""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import xlogy
from lotto645 import data_hash,load_draws_jsonl
from lotto_seed_128 import save

FEATURES=['numbers_at_most_31','adjacent_pairs','multiples_of_five','span']


def features(numbers):
    n=np.sort(np.asarray(numbers),axis=1)
    return np.column_stack([(n<=31).sum(axis=1),(np.diff(n,axis=1)==1).sum(axis=1),
                            (n%5==0).sum(axis=1),n[:,-1]-n[:,0]]).astype(float)


def fit_model(x,y,sales,ridge=1.):
    center=x.mean(axis=0); scale=x.std(axis=0); scale[scale==0]=1.
    design=np.column_stack([np.ones(len(x)),(x-center)/scale])
    sales_scale=float(sales.mean()); offset=np.log(sales/sales_scale)
    def objective(beta):
        eta=offset+design@beta; mu=np.exp(eta)
        loss=np.sum(mu-y*eta)+ridge*np.sum(beta[1:]**2)/2
        gradient=design.T@(mu-y)+ridge*np.r_[0,beta[1:]]
        return loss,gradient
    initial=np.zeros(design.shape[1]); initial[0]=np.log(y.sum()/np.exp(offset).sum())
    result=minimize(objective,initial,jac=True,method='BFGS',options={'gtol':1e-6})
    if not result.success and np.max(np.abs(result.jac))>1e-4:
        raise RuntimeError(result.message)
    return {'coefficients':result.x.tolist(),'center':center.tolist(),'scale':scale.tolist(),
            'sales_scale':sales_scale,'ridge':ridge,'gradient_max':float(np.max(np.abs(result.jac)))}


def predict(model,x,sales):
    design=np.column_stack([np.ones(len(x)),(x-np.array(model['center']))/np.array(model['scale'])])
    return np.exp(np.log(sales/model['sales_scale'])+design@np.array(model['coefficients']))


def deviance(y,mu):
    return 2*(xlogy(y,y/mu)-y+mu)


def block_interval(values,repeats=4999,seed=20261016,block=10):
    rng=np.random.default_rng(seed); n=len(values); means=[]
    for _ in range(repeats):
        starts=rng.integers(0,n,size=(n+block-1)//block)
        indices=((starts[:,None]+np.arange(block))%n).ravel()[:n]
        means.append(float(values[indices].mean()))
    return np.quantile(means,[.025,.975]).tolist()


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=root/'data/lotto/reports/bias-null/crowding')
    out=parser.parse_args().output
    if out.exists(): raise ValueError('refuse to overwrite results')
    rows=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    # Fixed later start avoids early launch-era changes; no claim that all time trends disappear.
    selected=[d for d in rows if d.draw_no>=201]
    if any(d.sales_amount is None or d.sales_amount<=0 or d.winner_count_by_rank.get('1') is None for d in selected):
        raise ValueError('complete positive sales and winner counts required')
    protocol={'trigger':'No Holm-significant bias tests and seed null comparison not significant at .05',
        'features':FEATURES,'train':[201,740],'validation':[741,990],'final':[991,rows[-1].draw_no],
        'model':'Poisson mean regression with log-sales offset; standardized training features; ridge=1',
        'baseline':'same training rows, intercept and log-sales offset only',
        'metrics':'heldout mean Poisson deviance, MAE; positive baseline-minus-model deviance gain is better',
        'uncertainty':'4999 circular block bootstraps, block length 10; descriptive fixed-model intervals',
        'limitations':'post-fallback exploratory analysis, not a profit estimate or a ticket-selection validation',
        'ledger_hash':data_hash(rows)}
    out.mkdir(parents=True); save(out/'protocol.json',protocol)
    x=features([d.numbers_sorted for d in selected]); y=np.array([d.winner_count_by_rank['1'] for d in selected],dtype=float)
    sales=np.array([d.sales_amount for d in selected],dtype=float); ids=np.array([d.draw_no for d in selected])
    training=ids<=740; model=fit_model(x[training],y[training],sales[training]); baseline=fit_model(x[training,:0],y[training],sales[training])
    prediction=predict(model,x,sales); reference=predict(baseline,x[:,:0],sales)
    results={}
    for label,mask in [('validation',(ids>740)&(ids<=990)),('final',ids>990)]:
        actual=y[mask]; pm=prediction[mask]; pb=reference[mask]
        gain=deviance(actual,pb)-deviance(actual,pm)
        results[label]={'draws':int(mask.sum()),'model_mean_deviance':float(deviance(actual,pm).mean()),
            'baseline_mean_deviance':float(deviance(actual,pb).mean()),'mean_deviance_gain':float(gain.mean()),
            'gain_block_bootstrap_interval_95':block_interval(gain),
            'model_mae':float(np.abs(actual-pm).mean()),'baseline_mae':float(np.abs(actual-pb).mean())}
    save(out/'result.json',{'protocol':protocol,'model':model,'baseline':baseline,'results':results,
        'heldout_predictions':[{'draw':int(n),'actual_winners':int(a),'model':float(m),'baseline':float(b)}
             for n,a,m,b in zip(ids[~training],y[~training],prediction[~training],reference[~training])]})
    print(json.dumps(results,indent=2))


if __name__=='__main__': main()
