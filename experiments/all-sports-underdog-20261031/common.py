"""Fixed full-period outcome model and independent sport-worker entry point."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
from collections import defaultdict,deque
from datetime import date,timedelta
from decimal import Decimal
import gzip,hashlib,json,math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp,softmax

MODELS=('market','outcome_form','market_residual','half_market_form')
POLICIES=('all','favorites','underdogs','market_same_underdogs')
K={'bs':12.,'sc':24.,'bk':16.,'vl':18.}


def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]


def save(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def dump(path,rows):
    with Path(path).open('wb') as out:
        with gzip.GzipFile(fileobj=out,mode='wb',filename='',mtime=0) as z:
            for r in rows:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())


def classify(pick,q):
    mx=max(q)
    return 'underdog' if q[pick]<mx-1e-12 else 'top_tie' if sum(abs(v-mx)<=1e-12 for v in q)>1 else 'favorite'


def causal_features(rows):
    ordered=sorted(rows,key=lambda r:(r['day'],r['kickoff'],r['id']))
    if len({r['id'] for r in ordered})!=len(ordered):raise ValueError('duplicate fixture')
    rating=defaultdict(lambda:1500.);recent=defaultdict(lambda:deque(maxlen=20));league=defaultdict(lambda:np.ones(3))
    pending=deque();features=[]
    for r in ordered:
        today=date.fromisoformat(r['day'])
        while pending and pending[0][0]<=today:
            _,old=pending.popleft();s=old['sport'];h=(s,old['league'],old['home']);a=(s,old['league'],old['away'])
            actual=1. if old['y']==0 else .5 if s=='sc' and old['y']==1 else 0.
            p=1/(1+10**(-(rating[h]+45-rating[a])/400))
            change=K[s]*(actual-p);rating[h]+=change;rating[a]-=change
            recent[h].append(actual);recent[a].append(1-actual)
            league[(s,old['league'])][old['y']]+=1
        s=r['sport'];h=(s,r['league'],r['home']);a=(s,r['league'],r['away']);counts=league[(s,r['league'])]
        rates=counts[:3 if s=='sc' else 2];rates=rates/rates.sum()
        form=lambda team:float(np.mean(recent[team])) if recent[team] else .5
        features.append([(rating[h]+45-rating[a])/400,form(h)-form(a),float(rates[0]),float(rates[1]) if s=='sc' else 0.])
        pending.append((today+timedelta(days=4),r))
    return ordered,np.array(features,dtype=float)


def train(x,y,q,offset):
    means=x.mean(0);scales=x.std(0);scales=np.where(scales>1e-12,scales,1.)
    design=np.column_stack([np.ones(len(x)),(x-means)/scales]);k=q.shape[1];labels=np.eye(k)[y]
    z=np.log(q) if offset else np.zeros_like(q)
    def objective(flat):
        b=flat.reshape(design.shape[1],k);v=z+design@b;pen=b.copy();pen[0]=0
        loss=float(np.mean(logsumexp(v,axis=1)-v[np.arange(len(y)),y]))+.01*float((pen*pen).sum())
        grad=design.T@(softmax(v,axis=1)-labels)/len(y)+.02*pen
        return loss,grad.ravel()
    opt=minimize(objective,np.zeros(design.shape[1]*k),jac=True,method='L-BFGS-B',options={'maxiter':600,'ftol':1e-12,'gtol':1e-8})
    if not opt.success:raise RuntimeError('optimizer failed: '+opt.message)
    return {'mean':means.tolist(),'scale':scales.tolist(),'beta':opt.x.reshape(design.shape[1],k).tolist(),'offset':offset,'training_rows':len(y)}


def predict(state,x,q):
    design=np.column_stack([np.ones(len(x)),(x-np.array(state['mean']))/np.array(state['scale'])])
    return softmax(design@np.array(state['beta'])+(np.log(q) if state['offset'] else 0),axis=1)


def forecasts(rows):
    rows,x=causal_features(rows)
    if not rows:return [],[]
    if len({r['sport'] for r in rows})!=1 or len({r['cohort'] for r in rows})!=1:raise ValueError('one sport/cohort per worker')
    q=np.array([[1/o for o in r['odds']] for r in rows]);q/=q.sum(1,keepdims=True)
    y=np.array([r['y'] for r in rows],int);days=np.array([r['day'] for r in rows]);months=np.array([d[:7] for d in days])
    result=[];fits=[]
    for month in sorted(set(months)):
        start=date.fromisoformat(month+'-01');cutoff=(start-timedelta(days=4)).isoformat()
        train_idx=np.where(days<=cutoff)[0];test_idx=np.where(months==month)[0]
        eligible=len(train_idx)>=200 and len(set(y[train_idx]))==q.shape[1]
        fitted={name:train(x[train_idx],y[train_idx],q[train_idx],name=='market_residual') for name in ('outcome_form','market_residual')} if eligible else {}
        pp={'market':q[test_idx]}
        for name in ('outcome_form','market_residual'):pp[name]=predict(fitted[name],x[test_idx],q[test_idx]) if eligible else q[test_idx].copy()
        pp['half_market_form']=(q[test_idx]+pp['outcome_form'])/2
        state_hash=hashlib.sha256(json.dumps(fitted,sort_keys=True).encode()).hexdigest()
        fits.append(dict(month=month,training_cutoff_inclusive=cutoff,training_rows=len(train_idx),fallback=not eligible,state_sha256=state_hash,states=fitted))
        for j,i in enumerate(test_idx):
            r=rows[i];item={**r,'market_p':q[i].tolist(),'fallback':not eligible,'month_state_sha256':state_hash,'predictions':{}}
            for name,p in pp.items():
                v=p[j];pick=int(np.argmax(v))
                if not np.isfinite(v).all() or abs(v.sum()-1)>1e-10:raise ValueError('invalid forecast')
                item['predictions'][name]={'p':v.tolist(),'pick':pick,'group':classify(pick,q[i]),'rank':int(sum(q[i]>q[i,pick]+1e-12)+1),
                                          'draw':r['sport']=='sc' and pick==1,'hit':pick==r['y'],
                                          'net':str(Decimal(str(r['odds'][pick]))-1 if pick==r['y'] else Decimal(-1))}
            result.append(item)
    return sorted(result,key=lambda r:(r['day'],r['kickoff'],r['id'])),fits


def metrics(rows,model,policy):
    take=[r for r in rows if policy=='all' or policy=='favorites' and r['predictions'][model]['group']=='favorite' or policy in ('underdogs','market_same_underdogs') and r['predictions'][model]['group']=='underdog']
    use='market' if policy=='market_same_underdogs' else model
    net=sum((Decimal(r['predictions'][use]['net']) for r in take),Decimal(0));n=len(take)
    daily={r['day']:Decimal(0) for r in rows};monthly={d[:7]:Decimal(0) for d in daily}
    for r in take:daily[r['day']]+=Decimal(r['predictions'][use]['net']);monthly[r['day'][:7]]+=Decimal(r['predictions'][use]['net'])
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):
        balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    return dict(opportunities=len(rows),bets=n,hits=sum(r['predictions'][use]['hit'] for r in take),
                accuracy=sum(r['predictions'][use]['hit'] for r in take)/n if n else None,
                net_units=str(net),roi=float(net/n) if n else None,net_per_opportunity=float(net/len(rows)) if rows else None,
                drawdown_units=float(dd),monthly_net={m:float(v) for m,v in sorted(monthly.items())},
                positive_months=sum(v>0 for v in monthly.values()),negative_months=sum(v<0 for v in monthly.values()))


def interval(rows,model,vs_market=False):
    selected=[r for r in rows if r['predictions'][model]['group']=='underdog']
    blocks=defaultdict(lambda:np.zeros(2))
    for r in selected:
        week=date.fromisoformat(r['day']).isocalendar()[:2]
        net=Decimal(r['predictions'][model]['net'])-(Decimal(r['predictions']['market']['net']) if vs_market else Decimal(0))
        blocks[week]+=np.array([1.,float(net)])
    if not blocks:return dict(n=0,weeks=0,mean=None,ci95=None)
    a=np.array([blocks[k] for k in sorted(blocks)]);rng=np.random.default_rng(20261031)
    sample=a[rng.integers(0,len(a),size=(2000,len(a)))].sum(1);v=sample[:,1]/sample[:,0]
    return dict(n=len(selected),weeks=len(a),mean=float(a[:,1].sum()/len(selected)),ci95=np.quantile(v,[.025,.975]).tolist())


def summarize(rows,uncertainty=False):
    output={m:{'policies':{p:metrics(rows,m,p) for p in POLICIES}} for m in MODELS}
    if uncertainty:
        for m in MODELS:
            output[m]['underdog_vs_skip']=interval(rows,m)
            output[m]['underdog_vs_market']=interval(rows,m,True)
    if rows and rows[0]['sport']=='sc':
        for m in MODELS:
            output[m]['underdog_breakdown']={tag:metrics([r for r in rows if r['predictions'][m]['group']=='underdog' and (r['predictions'][m]['rank']==rank if rank else r['predictions'][m]['draw'])],m,'all') for tag,rank in [('rank2',2),('rank3',3),('draw',None)]}
    return output


def run(inputs,sport,output):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for path in inputs:rows.extend([r for r in read(path) if r['sport']==sport])
    cohorts={}
    for c in sorted({r['cohort'] for r in rows}):
        records,states=forecasts([r for r in rows if r['cohort']==c]);folder=out/c;folder.mkdir(exist_ok=True)
        dump(folder/'ledger.jsonl.gz',records);save(folder/'states.json',states)
        dates=sorted({r['day'] for r in records});split=dates[len(dates)//2]
        report=dict(sport=sport,cohort=c,count=len(records),range=[dates[0],dates[-1]],fallback_events=sum(r['fallback'] for r in records),
            full=summarize(records,True),by_year={year:summarize([r for r in records if r['day'][:4]==year],True) for year in sorted({r['day'][:4] for r in records})},
            by_league={l:summarize([r for r in records if r['league']==l]) for l in sorted({r['league'] for r in records})},
            chronological_split_day=split,first_half=summarize([r for r in records if r['day']<split],True),second_half=summarize([r for r in records if r['day']>=split],True),
            worker_input_sha256={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in inputs},
            common_lf_sha256=hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
            warning='Retrospective source/price/clock uncertified. First period fallback explicit. Daily-close PnL uses source game date, not witnessed settlement. No operating/tipster9:1 claim.')
        save(folder/'results.json',report);cohorts[c]={'count':len(records),'range':report['range'],'fallback':report['fallback_events'],
            'roi':{m:report['full'][m]['policies']['all']['roi'] for m in MODELS},'under_net':{m:report['full'][m]['policies']['underdogs']['net_units'] for m in MODELS}}
    print(json.dumps(cohorts,ensure_ascii=False));return cohorts


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--input',action='append',required=True);ap.add_argument('--sport',choices=list(K),required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();run(a.input,a.sport,a.output)
