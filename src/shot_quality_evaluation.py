"""Conditional shot-outcome diagnostic, NOT a pregame outcome backtest."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit


def model(train,test,context):
    def matrix(rows):
        x=np.array([[np.clip(r['score_diff'],-2,2),np.clip(r['man_diff'],-2,2),r['minute']/90,r['home']] for r in rows])
        return x if context else np.zeros((len(rows),4))
    x=matrix(train);xt=matrix(test);mean=x.mean(0);sd=x.std(0);sd[sd<1e-8]=1
    x=np.c_[np.ones(len(x)),(x-mean)/sd];xt=np.c_[np.ones(len(xt)),(xt-mean)/sd]
    probabilities=np.clip([r['xg'] for r in train],1e-6,1-1e-6)
    offset=np.log(probabilities/(1-probabilities));y=np.array([r['goal'] for r in train])
    def loss(b):
        z=offset+x@b;p=expit(z);g=x.T@(p-y);g[1:]+=10*b[1:]
        return np.sum(np.logaddexp(0,z)-y*z)+5*np.sum(b[1:]**2),g
    opt=minimize(loss,np.zeros(x.shape[1]),jac=True,method='L-BFGS-B')
    if not opt.success:raise ValueError('optimization failed')
    p=np.clip([r['xg'] for r in test],1e-6,1-1e-6)
    return expit(np.log(p/(1-p))+xt@opt.x)


def evaluate(rows):
    rows=[r for r in rows if not r['penalty']]
    dates=sorted({r['day'] for r in rows})
    if len(dates)<3:return {'status':'insufficient_dates'},{}
    boundary=dates[max(1,int(len(dates)*.7))]
    train=[r for r in rows if r['day']<boundary];test=[r for r in rows if r['day']>=boundary]
    if min(len(train),len(test))<30:return {'status':'insufficient_shots'},{}
    y=np.array([r['goal'] for r in test])
    prior=(sum(r['goal'] for r in train)+1)/(len(train)+2)
    predictions={'constant':np.full(len(test),prior),'provider_xg':np.array([r['xg'] for r in test]),
                 'xg_calibration':model(train,test,False),'xg_context':model(train,test,True)}
    metrics={}
    for name,p in predictions.items():
        p=np.clip(p,1e-6,1-1e-6)
        metrics[name]={'brier':float(np.mean((p-y)**2)),
                       'log_loss':float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),
                       'expected_goals':float(p.sum()),'actual_goals':int(y.sum())}
    report={'status':'conditional_shot_pilot_only','production_allowed':False,'split_date':boundary,
            'train_games':len({r['match_id'] for r in train}),'test_games':len({r['match_id'] for r in test}),
            'train_shots':len(train),'test_shots':len(test),'test_dates':len({r['day'] for r in test}),
            'metrics':metrics,'warning':'Shot context is known at the shot, not pregame. Provider xG model vintage is unverified. No win hit rate claim.'}
    return report,{'test_shots':test,'predictions':{k:v.tolist() for k,v in predictions.items()}}


def run(source,output):
    if output.exists():raise FileExistsError(output)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('select status from run').fetchone()[0]!='complete':raise ValueError('incomplete collection')
        rows=[json.loads(r[0]) for r in db.execute('SELECT data FROM shots')]
    report,predictions=evaluate(rows)
    metadata={'input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'penalty':10}
    output.mkdir(parents=True,exist_ok=False)
    with closing(sqlite3.connect(output/'evaluation.sqlite3')) as db,db:
        db.execute('CREATE TABLE result(metadata TEXT,report TEXT,predictions TEXT)')
        db.execute('INSERT INTO result VALUES(?,?,?)',(json.dumps(metadata),json.dumps(report),json.dumps(predictions)))
    (output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(run(a.source,a.output)))
