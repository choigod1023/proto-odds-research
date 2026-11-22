"""Separated development selection, label-free prediction, and later outcome scoring."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
import argparse, hashlib, importlib.util, json, math, subprocess, sys, time, warnings
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import residual_models as residual
import ml_models as ml
_spec=importlib.util.spec_from_file_location('nested_search_local_features',HERE/'features.py')
_features=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_features)
read=_features.read
OUTPUT_ROOT=None


def outputs():return OUTPUT_ROOT if OUTPUT_ROOT is not None else HERE

FOLDS=(('2025-02-01','2025-06-30'),('2025-07-01','2025-08-31'),('2025-09-01','2025-11-30'))
PRIORITY={'market':0,'dc':1,'pool':2,'residual':3,'logistic':4,'random_forest':5,'hist_gradient_boosting':6}


def configurations():
    c=[{'id':'market','family':'market'},{'id':'dc','family':'dc'}]+residual.configurations()+residual.pooling_configs()+ml.configurations()
    if len(c)!=4334 or len({x['id'] for x in c})!=4334:raise ValueError('fixed search space changed')
    return c


def dump(path,obj):
    with path.open('x',encoding='utf8',newline='\n') as f:json.dump(obj,f,indent=2,allow_nan=False);f.write('\n')


def write_lines(path,rows):
    with path.open('x',encoding='utf8',newline='\n') as f:
        for r in rows:f.write(json.dumps(r,sort_keys=True,allow_nan=False)+'\n')


def digest(path):return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def paths():
    return [ROOT/'docs/research/2026-11-02-nested-search-protocol.md']+[HERE/x for x in
     ('search.py','features.py','ml_models.py','residual_models.py','prepared/development.jsonl','prepared/outer-features.jsonl','prepared/manifest.json')]


def freeze_check(commit):
    subprocess.run(['git','merge-base','--is-ancestor',commit,'HEAD'],cwd=ROOT,check=True)
    for path in paths():
        b=subprocess.check_output(['git','show',commit+':'+path.relative_to(ROOT).as_posix()],cwd=ROOT)
        if b.replace(b'\r\n',b'\n')!=path.read_bytes().replace(b'\r\n',b'\n'):raise ValueError('unregistered search code/input: '+str(path))


def array(rows,key):return np.asarray([r[key] for r in rows],dtype=float)


def predict_grid(train,target,configs):
    if not train or not target:raise ValueError('empty split')
    if max(r['day'] for r in train)>(date.fromisoformat(min(r['day'] for r in target))-timedelta(days=7)).isoformat():
        raise ValueError('future/embargoed training data')
    if {r['id'] for r in train}&{r['id'] for r in target}:raise ValueError('train/target overlap')
    if any('y' in r for r in target):raise ValueError('target labels forbidden in prediction')
    market=array(target,'market_p');dc=array(target,'dc_p');out={};failed=[]
    for c in configs:
        if c['family'] in ('market','dc'):out[c['id']]=market if c['family']=='market' else dc
    rs=[c for c in configs if c['family']=='residual']
    if rs:
        out.update(residual.predict_all(array(train,'market_p'),array(train,'dc_p'),np.asarray([r['features']['form12'] for r in train]),
          np.asarray([r['y'] for r in train]),[r['day'] for r in train],[r['id'] for r in train],market,dc,
          np.asarray([r['features']['form12'] for r in target]),[r['day'] for r in target],rs))
    ps=[c for c in configs if c['family']=='pool']
    if ps:out.update(residual.predict_pool_all(market,dc,ps))
    groups=defaultdict(list)
    for c in configs:
        if c['family'] in ('logistic','random_forest','hist_gradient_boosting'):
            groups[(c['family'],c['feature_set'],json.dumps(c['params'],sort_keys=True))].append(c)
    tx={k:np.asarray([r['features'][k] for r in train]) for k in ('base','disagreement','form')}
    qx={k:np.asarray([r['features'][k] for r in target]) for k in ('base','disagreement','form')}
    from sklearn.exceptions import ConvergenceWarning
    for cs in groups.values():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',ConvergenceWarning)
                out.update(ml.predict_all(tx,np.asarray([r['y'] for r in train]),qx,market,cs))
        except (ValueError,RuntimeError,ConvergenceWarning) as e:
            failed.extend({'id':c['id'],'error_type':type(e).__name__,'error':str(e)[:250]} for c in cs)
    for ident,p in out.items():
        if p.shape!=(len(target),3) or not np.isfinite(p).all() or (p<=0).any() or not np.allclose(p.sum(1),1,atol=1e-8):
            raise ValueError('invalid predicted probabilities: '+ident)
    return out,failed


def metrics(p,rows):
    y=np.asarray([r['y'] for r in rows]);pick=p.argmax(1);hit=pick==y
    nets=[Decimal(r['prices'][int(k)])-1 if h else Decimal(-1) for r,k,h in zip(rows,pick,hit)]
    daily=defaultdict(Decimal);monthly=defaultdict(Decimal)
    for r,n in zip(rows,nets):daily[r['day']]+=n;monthly[r['day'][:7]]+=n
    balance=peak=dd=Decimal(0)
    for d in sorted(daily):balance+=daily[d];peak=max(peak,balance);dd=max(dd,peak-balance)
    return {'games':len(rows),'hits':int(hit.sum()),'accuracy':float(hit.mean()),'net_units':str(sum(nets,Decimal(0))),
      'roi':float(sum(nets,Decimal(0))/len(rows)),'logloss':float(-np.log(p[np.arange(len(rows)),y]).mean()),
      'brier':float(((p-np.eye(3)[y])**2).sum(1).mean()),'daily_close_max_drawdown_units':str(dd),
      'monthly_units':{k:str(v) for k,v in sorted(monthly.items())}}


def choose(trials):
    valid=[r for r in trials if r['status']=='ok']
    if not valid:raise ValueError('no complete candidates')
    return min(valid,key=lambda r:(-r['metrics']['hits'],r['metrics']['logloss'],PRIORITY[r['config']['family']],r['id']))


def development(registration):
    freeze_check(registration);output=outputs()/'development';output.mkdir(exist_ok=False)
    started=time.monotonic();data=read(HERE/'prepared/development.jsonl');configs=configurations()
    byid={c['id']:[] for c in configs};allval=[];folds=[];failures=[]
    for start,end in FOLDS:
        cutoff=(date.fromisoformat(start)-timedelta(days=7)).isoformat()
        train=[r for r in data if r['day']<=cutoff];val=[r for r in data if start<=r['day']<=end]
        target=[{k:v for k,v in r.items() if k!='y'} for r in val]
        preds,bad=predict_grid(train,target,configs);failures.extend({**r,'fold':start} for r in bad)
        for c in configs:
            if c['id'] in preds:byid[c['id']].append(preds[c['id']])
        folds.append({'start':start,'end':end,'cutoff':cutoff,'training_ids':[r['id'] for r in train],
          'target_ids':[r['id'] for r in val],'training_n':len(train),'target_n':len(val),'prediction_count':len(preds),'failed':len(bad)})
        allval.extend(val)
        print(json.dumps({'fold':start,'training':len(train),'validation':len(val),'configs':len(preds),'failed':len(bad),'elapsed':time.monotonic()-started}),flush=True)
    if len(allval)!=227 or len({r['id'] for r in allval})!=227:raise ValueError('development validation coverage')
    trials=[]
    for c in configs:
        ps=byid[c['id']]
        trials.append({'id':c['id'],'config':c,'status':'ok' if len(ps)==3 else 'failed',
          'metrics':metrics(np.vstack(ps),allval) if len(ps)==3 else None})
    selected=choose(trials);p=np.vstack(byid[selected['id']])
    write_lines(output/'trials.jsonl',trials);dump(output/'folds.json',folds);dump(output/'failures.json',failures)
    unique=len({hashlib.sha256(np.vstack(v).round(12).tobytes()).hexdigest() for v in byid.values() if len(v)==3})
    dump(output/'selected-model.json',{'registration':registration,'config':selected['config'],'development_metrics':selected['metrics'],
      'total_definitions':len(configs),'complete_trials':sum(r['status']=='ok' for r in trials),'unique_probability_vectors_rounded12':unique,
      'elapsed_seconds':time.monotonic()-started,'classification':'development winner from broad search; NOT independent validated accuracy',
      'source_hashes':{p.relative_to(ROOT).as_posix():digest(p) for p in paths()}})
    write_lines(output/'selected-validation.jsonl',[dict(id=r['id'],day=r['day'],p=x.tolist(),y=r['y'],prices=r['prices']) for r,x in zip(allval,p)])
    print(json.dumps(selected),flush=True)


def future_predict(registration):
    freeze_check(registration);selected=json.loads((outputs()/'development/selected-model.json').read_text(encoding='utf8'))
    train=[r for r in read(HERE/'prepared/development.jsonl') if r['day']<='2026-01-25']
    target=read(HERE/'prepared/outer-features.jsonl');configs=[{'id':'market','family':'market'},{'id':'dc','family':'dc'}]
    if selected['config']['id'] not in ('market','dc'):configs.append(selected['config'])
    preds,bad=predict_grid(train,target,configs)
    if bad:raise ValueError('selected refit failed; no outcome-based replacement')
    out=outputs()/'outer-predictions';out.mkdir(exist_ok=False)
    write_lines(out/'predictions.jsonl',[dict(id=r['id'],day=r['day'],prices=r['prices'],
      predictions={name:p[i].tolist() for name,p in preds.items()}) for i,r in enumerate(target)])
    dump(out/'manifest.json',{'selected':selected['config'],'training_ids':[r['id'] for r in train],
      'training_cutoff':'2026-01-25','selected_artifact_sha256':digest(outputs()/'development/selected-model.json'),
      'target_features_sha256':digest(HERE/'prepared/outer-features.jsonl'),'target_labels_read':False})
    print(json.dumps({'selected':selected['config']['id'],'predicted':len(target),'target_labels_read':False}))


def score():
    predictions=read(outputs()/'outer-predictions/predictions.jsonl');labels={r['id']:r['y'] for r in read(HERE/'prepared/outer-labels.jsonl')}
    if set(labels)!={r['id'] for r in predictions}:raise ValueError('label identity mismatch')
    rows=[dict(id=r['id'],day=r['day'],prices=r['prices'],y=labels[r['id']]) for r in predictions]
    names=list(predictions[0]['predictions']);ms={name:metrics(np.asarray([r['predictions'][name] for r in predictions]),rows) for name in names}
    out=outputs()/'outer-results';out.mkdir(exist_ok=False)
    dump(out/'results.json',{'classification':'previously_seen_2026_outer_diagnostic_not_blind','metrics':ms,
      'selected_artifact_sha256':digest(outputs()/'development/selected-model.json'),
      'prediction_sha256':digest(outputs()/'outer-predictions/predictions.jsonl'),'automatic_adoption':False,
      'actual_wallet_replay':False,'monthly_loss_cap_changed':False})
    print(json.dumps(ms))


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('stage',choices=['development','predict','score']);a.add_argument('--registration');a.add_argument('--output-root',type=Path);args=a.parse_args()
    if args.output_root:
        OUTPUT_ROOT=args.output_root.resolve();OUTPUT_ROOT.mkdir(parents=True,exist_ok=True)
    if args.stage!='score' and not args.registration:a.error('--registration is required')
    {'development':lambda:development(args.registration),'predict':lambda:future_predict(args.registration),'score':score}[args.stage]()
