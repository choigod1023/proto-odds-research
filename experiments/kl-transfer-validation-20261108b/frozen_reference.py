"""Retrospective score-grid experiment. No network or production state."""
import sys, json, re, hashlib, argparse
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'src'))
from accuracy_formula_lab import load_unique_market_rows
from matches import load_matches
EXPECTED='9ffddb2dcbc4297c32e6ad3826f392e1842e52e10b509e4cbe47eed1f84b9da4'
H,A=np.indices((11,11)); H=H.ravel(); A=A.ravel()
def masks(fam,line):
    d=H-A if fam=='승무패' else H+line-A if fam=='핸디캡' else H+A-line
    if fam=='언더오버': return np.array([d<0,d>0],float) # equality is a push
    return np.array([d>0,d==0,d<0],float)
def poisson(lh,la,rho=0):
    q=np.exp(H*np.log(lh)-lh-gammaln(H+1)+A*np.log(la)-la-gammaln(A+1))
    tau=np.ones_like(q); tau[(H==0)&(A==0)]=1-lh*la*rho; tau[(H==0)&(A==1)]=1+lh*rho; tau[(H==1)&(A==0)]=1+la*rho; tau[(H==1)&(A==1)]=1-rho
    if min(tau)<=0: return None
    q*=tau; return q/q.sum()
def devig(odds):
    p=1/np.array(odds); return p/p.sum()
def fit(offers,dc=False):
    def loss(x):
        q=poisson(np.exp(x[0]),np.exp(x[1]),x[2] if dc else 0)
        if q is None:return 1e4
        return sum(np.sum((m@q-t)**2) for m,t in offers)+(0.02*x[2]**2 if dc else 0)
    x=[np.log(1.4),np.log(1.1)]+([0] if dc else [])
    res=minimize(loss,x,method='L-BFGS-B',bounds=[(-2,1.3),(-2,1.3)]+([(-0.05,0.05)] if dc else []),options={'ftol':1e-10})
    if not res.success: raise RuntimeError(res.message)
    return poisson(np.exp(res.x[0]),np.exp(res.x[1]),res.x[2] if dc else 0)
def project(q,offers,weight=10.):
    M=np.concatenate([x[0] for x in offers]); t=np.concatenate([x[1] for x in offers]); logq=np.log(q)
    def f(v):
        z=logq+v@M; s=logsumexp(z); p=np.exp(z-s)
        return s-v@t+v@v/(4*weight), M@p-t+v/(2*weight)
    res=minimize(f,np.zeros(len(t)),jac=True,method='BFGS',options={'gtol':1e-8})
    if np.linalg.norm(res.jac)>1e-5: raise RuntimeError('projection convergence')
    z=logq+res.x@M; return np.exp(z-logsumexp(z))
def choose_outcome(fam,line,h,a):
    d=h-a if fam=='승무패' else h+line-a if fam=='핸디캡' else h+a-line
    if fam=='언더오버':return None if d==0 else (0 if d<0 else 1)
    return 0 if d>0 else 1 if d==0 else 2

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--raw',required=True);ap.add_argument('--out',required=True); args=ap.parse_args()
    raw=Path(args.raw); sha=hashlib.sha256(raw.read_bytes()).hexdigest()
    if sha!=EXPECTED:raise ValueError('unexpected input hash')
    records,quality=load_unique_market_rows(raw); matches=load_matches(sports=('sc',),path=raw)
    scores={(r.kickoff,r.league,r.home_team,r.away_team):(r.home_score,r.away_score) for r in matches.itertuples(index=False)}
    grouped=defaultdict(lambda:defaultdict(list)); exclusions=Counter()
    for r in records:
        t,s,league,h,a,fam,booking,label,n=r['key']
        if s!='sc' or league not in ('K리그1','K리그2') or fam not in ('승무패','핸디캡','언더오버'):continue
        if (fam=='승무패' and n!=3) or (fam=='핸디캡' and n!=3) or (fam=='언더오버' and n!=2):continue
        line=None
        if fam!='승무패':
            match=re.fullmatch(r'[HU]\s+([+-]?\d+(?:\.\d+)?)',label)
            if not match:continue
            line=float(match.group(1))
            if fam=='핸디캡' and line%1:continue
            if fam=='언더오버' and line%1!=.5:continue
        key=(t,league,h,a); score=scores.get(key)
        if score is None or choose_outcome(fam,line,*score)!=r['winner']:exclusions['score_or_result_conflict']+=1;continue
        grouped[key][fam].append({'family':fam,'line':line,'odds':r['odds'],'outcome':r['winner'],'booking':booking})
    rows=[]; counts=Counter()
    for key,groups in sorted(grouped.items()):
        if any(len(groups[f])!=1 for f in ('승무패','핸디캡','언더오버')):
            counts['missing_or_ambiguous_markets']+=1;continue
        offers=[groups[f][0] for f in ('승무패','핸디캡','언더오버')]; mt=[(masks(o['family'],o['line']),devig(o['odds'])) for o in offers]
        q1=fit(mt[:1]); qa=fit(mt); qd=fit(mt,True); qp=project(q1,mt)
        for name,q in [('poisson_1x2',q1),('poisson_all',qa),('dc_all',qd),('kl_soft',qp)]:
            if not np.isfinite(q).all() or min(q)<0 or abs(sum(q)-1)>1e-10:raise ValueError('bad distribution')
            for o,(m,target) in zip(offers,mt):
                probs=m@q; pick=int(np.argmax(probs)); y=o['outcome']; profit=o['odds'][pick]-1 if pick==y else -1
                rows.append({'date':str(key[0]),'league':key[1],'home':key[2],'away':key[3],'score':scores[key], 'model':name,'family':o['family'],'line':o['line'],'booking':o['booking'],'odds':o['odds'],'market_devig':target.tolist(),'probabilities':probs.tolist(),'outcome':y,'pick':pick,'profit':profit})
        for o, (_, target) in zip(offers, mt):
            pick=int(np.argmax(target)); y=o['outcome']; profit=o['odds'][pick]-1 if pick==y else -1
            rows.append({'date':str(key[0]),'league':key[1],'home':key[2],'away':key[3],'score':scores[key], 'model':'market_devig','family':o['family'],'line':o['line'],'booking':o['booking'],'odds':o['odds'],'market_devig':target.tolist(),'probabilities':target.tolist(),'outcome':y,'pick':pick,'profit':profit})
        counts['matches']+=1
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    (out/'predictions.json').write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':')),encoding='utf8')
    def metrics(rr):
        if not rr:return None
        p=np.array([x['probabilities'] for x in rr]); y=np.array([x['outcome'] for x in rr]); one=np.eye(p.shape[1])[y]; profit=np.array([x['profit'] for x in rr]); days=defaultdict(float)
        for x in rr:days[x['date'][:10]]+=x['profit']
        cum=np.cumsum([days[x] for x in sorted(days)]); dd=float(np.max(np.maximum.accumulate(np.r_[0,cum])-np.r_[0,cum]))
        return {'n':len(rr),'wins':int(sum(x['pick']==x['outcome'] for x in rr)),'hit':float(np.mean([x['pick']==x['outcome'] for x in rr])),'roi':float(profit.mean()),'logloss':float(-np.log(p[np.arange(len(y)),y]).mean()),'brier':float(((p-one)**2).sum(axis=1).mean()),'max_daily_drawdown_units':dd}
    summary={}
    for name in ('poisson_1x2','poisson_all','dc_all','kl_soft','market_devig'):
        summary[name]={}
        for fam in ('승무패','핸디캡','언더오버'):
            rr=[r for r in rows if r['model']==name and r['family']==fam]; summary[name][fam]={k:metrics(v) for k,v in [('all',rr),('2023-2024',[x for x in rr if x['date'][:4]<='2024']),('2025-2026',[x for x in rr if x['date'][:4]>='2025']),('K리그1',[x for x in rr if x['league']=='K리그1']),('K리그2',[x for x in rr if x['league']=='K리그2'])]}
    result={'input_sha256':sha,'quality':quality,'counts':dict(counts),'exclusions':dict(exclusions),'period':[min(r['date'] for r in rows),max(r['date'] for r in rows)],'summary':summary}
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
