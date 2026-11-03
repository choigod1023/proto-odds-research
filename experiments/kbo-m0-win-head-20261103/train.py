"""One prespecified winner residual; evaluation prices/outcomes are not loaded."""
import argparse,copy,gzip,hashlib,json,math,sys
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit

P=Path(__file__).resolve().parent
ROOT=P.parents[1]
OLD=ROOT/'experiments/kbo-m0-followup-20261103'
sys.path.insert(0,str(OLD/'vendor/evolution/common'))
import core
INDEXES=(0,2,5,6,7)

def design(rows,spec):
    teams=spec['teams'];mean=np.asarray(spec['mean'])[list(INDEXES)];scale=np.asarray(spec['scale'])[list(INDEXES)]
    result=[]
    for row in rows:
        raw=np.asarray(core.legacy.scalar_features(row),dtype=float)[:,INDEXES]
        missing=~np.isfinite(raw);z=(np.where(missing,mean,raw)-mean)/scale
        result.append([1.]+[float(row['home_id']==t)-float(row['away_id']==t) for t in teams]+(z[0]-z[1]).tolist()+(missing[0].astype(float)-missing[1]).tolist()+(missing[0].astype(float)+missing[1]).tolist())
    return np.asarray(result,dtype=float)

def objective(beta,X,offset,y,pen):
    z=offset+X@beta
    return float(np.logaddexp(0,z).sum()-y@z+.5*np.sum(pen*beta*beta)),X.T@(expit(z)-y)+pen*beta

def fit(rows,labels,baseline):
    # Only these labels can influence the fit, regardless of caller mapping size.
    decisive=[];ys=[]
    for i,row in enumerate(rows):
        label=labels[row['fixture_id']];h,a=label['home_score'],label['away_score']
        if label['status']!='final' or type(h)is not int or type(a)is not int or min(h,a)<0:raise ValueError('invalid training result')
        if h!=a:decisive.append(i);ys.append(float(h>a))
    if not decisive:raise ValueError('no decisive training rows')
    X=design(rows,baseline['spec'])[decisive]
    predictions=core.predict_baseline(baseline,rows)
    offset=logit(np.clip([predictions[i]['probabilities_2way']['home'] for i in decisive],1e-12,1-1e-12))
    pen=np.full(X.shape[1],10.);pen[0]=0
    sol=minimize(objective,np.zeros(X.shape[1]),args=(X,offset,np.asarray(ys),pen),method='L-BFGS-B',jac=True,options={'maxiter':2000,'ftol':1e-12,'gtol':1e-7})
    if not sol.success or not np.isfinite(sol.x).all():raise ValueError('winner-head optimization failed: '+str(sol.message))
    return {'beta':sol.x.tolist(),'spec':baseline['spec'],'n_train':len(rows),'decisive_train':len(decisive),'l2':10,'objective':float(sol.fun),'gradient_max':float(np.max(np.abs(sol.jac))),'iterations':int(sol.nit)}

def predict(model,baseline_predictions,rows):
    if [r['fixture_id'] for r in rows]!=[p['fixture_id'] for p in baseline_predictions]:raise ValueError('forecast identity mismatch')
    X=design(rows,model['spec']);q=expit(logit(np.clip([r['probabilities_2way']['home'] for r in baseline_predictions],1e-12,1-1e-12))+X@np.asarray(model['beta']))
    result=[]
    for p,home in zip(baseline_predictions,q):
        d=p['probabilities_3way']['draw'];r=copy.deepcopy(p)
        r['probabilities_2way']={'home':float(home),'away':float(1-home)}
        r['probabilities_3way']={'home':float((1-d)*home),'draw':d,'away':float((1-d)*(1-home))}
        result.append(r)
    return result

def read_labels(path):return core.unique([json.loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()])
def write(path,value):
    raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8')
    if str(path).endswith('.gz'):raw=gzip.compress(raw,mtime=0)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as out:out.write(raw)
    return hashlib.sha256(raw).hexdigest()

def main(year):
    core.verify();all_rows=core.rows('train-actual.jsonl.gz')
    target=[r for r in all_rows if r['season']==year] if year<2026 else core.rows('test-actual.jsonl.gz')
    target=sorted(target,key=lambda r:(r['event_date'],r['fixture_id']))
    cutoff=date.fromisoformat(target[0]['event_date'])-timedelta(days=7)
    training=sorted([r for r in all_rows if r['season']<year and date.fromisoformat(r['event_date'])<=cutoff],key=lambda r:(r['event_date'],r['fixture_id']))
    labels=read_labels(OLD/'inputs/fit2023-labels.jsonl.gz');sources={}
    for y in range(2024,year):
        path=OLD/f'data/labels-{y}.jsonl.gz';labels.update(read_labels(path));sources[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    if set(labels)!=set(core.unique(training)):raise ValueError('training labels do not match past fold')
    if set(labels)&set(core.unique(target)):raise ValueError('target outcome reached training')
    baseline=core.fit_baseline(training,labels);b=core.predict_baseline(baseline,target)
    model=fit(training,labels,baseline);c=predict(model,b,target)
    out=P/f'artifacts/{year}';model_sha=write(out/'models.json',{'baseline':baseline,'winner_head':model})
    pred_sha=write(out/'predictions.json.gz',{'baseline':b,'winner_head':c})
    manifest={'target_year':year,'train_years':sorted({r['season'] for r in training}),'n_train':len(training),'n_target':len(target),'training_label_sources':sources,'models_sha256':model_sha,'predictions_sha256':pred_sha,'target_labels_or_prices_loaded':False,'historical_period_previously_seen':True,'contract_sha256':hashlib.sha256((P/'contract.json').read_bytes()).hexdigest()}
    write(out/'manifest.json',manifest);print(json.dumps(manifest))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True,choices=(2024,2025,2026));main(ap.parse_args().year)
