"""Pre-registered, bounded market-residual nearest-neighbor comparison."""
import itertools,json,math
from collections import Counter,defaultdict
from datetime import date,timedelta
from decimal import Decimal
import numpy as np
import baseline as b

ROOT=b.ROOT
CONFIGS=list(itertools.product(('market','combined'),('uniform','distance'),(30,60),(30,120)))

def vector(r,space):
    return np.asarray(r['market_p']+(r['dc_p'] if space=='combined' else []),dtype=float)

def forecast(bank,target,cfg):
    if set(target)!={'market_p','dc_p'}: raise ValueError('unexpected prediction features')
    space,weight,k,a=cfg
    if cfg not in CONFIGS or len(bank)<k: raise ValueError('invalid configuration')
    x=vector(target,space)
    near=sorted(((float(np.linalg.norm(vector(r,space)-x)),r['id'],r) for r in bank),key=lambda z:(z[0],z[1]))[:k]
    w=np.array([1.0 if weight=='uniform' else 1/(d+0.05) for d,_,_ in near]);w*=k/w.sum()
    residual=np.zeros(3)
    for z,(_,_,r) in zip(w,near):
        residual+=z*(np.eye(3)[r['y']]-np.asarray(r['market_p']))
    p=np.maximum(np.asarray(target['market_p'])+residual/(k+a),1e-6)
    return (p/p.sum()).tolist()

def select(hist):
    train=[r for r in hist if date.fromisoformat(r['day'])<b.TRAIN]
    val=[r for r in hist if b.VAL<=date.fromisoformat(r['day'])<date(2025,1,1)]
    def score(ps):
        return {'hits':sum(b.pick(p)==r['y'] for p,r in zip(ps,val)),'logloss':sum(-math.log(p[r['y']]) for p,r in zip(ps,val))/len(val)}
    market=score([r['market_p'] for r in val]); records=[]
    for cfg in CONFIGS:
        m=score([forecast(train,{'market_p':r['market_p'],'dc_p':r['dc_p']},cfg) for r in val]);records.append({'config':list(cfg),**m})
    eligible=[(i,x) for i,x in enumerate(records) if x['hits']>market['hits'] and x['logloss']<=market['logloss']]
    chosen=min(eligible,key=lambda t:(-t[1]['hits'],t[1]['logloss'],t[0]))[1]['config'] if eligible else None
    return chosen,market,records

def build(rows):
    hist=[r for r in rows if r['split']=='meta_history_2024']; test=sorted((r for r in rows if r['split']=='evaluation_2025'),key=lambda r:(r['day'],r['id']))
    if len(hist)!=228 or len(test)!=227 or max(date.fromisoformat(r['day']) for r in hist)>date.fromisoformat(test[0]['day'])-timedelta(days=7): raise ValueError('invalid time split')
    chosen,vm,vr=select(hist);bank=b.prepare(hist);ledger=[]
    for r in test:
        x={'market_p':r['market_p'],'dc_p':r['dc_p']}
        probs={'market':r['market_p'],'dc':r['dc_p'],'original':b.predict(bank,x,60),'tuned':forecast(hist,x,tuple(chosen)) if chosen else r['market_p'],'home':[1.0,0.0,0.0]}
        preds={name:{'probability':p,'pick':b.pick(p)} for name,p in probs.items()}
        for v in preds.values():
            v['hit']=v['pick']==r['y'];v['net']=str(Decimal(r['prices'][v['pick']])-1 if v['hit'] else Decimal(-1))
        ledger.append({'id':r['id'],'day':r['day'],'prices':r['prices'],'result':r['y'],'forecasts':preds})
    metrics={}
    for name in probs:
        if name=='home':
            # metrics() needs finite class probabilities; report only meaningful home-pick statistics.
            temp=[{**r,'forecasts':{**r['forecasts'],'home':{**r['forecasts']['home'],'probability':[1/3]*3}}} for r in ledger]
            m=b.metrics(temp,name);m['logloss']=None;m['brier']=None
        else:m=b.metrics(ledger,name)
        m['classes']=dict(Counter(('home','draw','away')[r['forecasts'][name]['pick']] for r in ledger))
        metrics[name]=m
    pairs={}
    for name in ('original','tuned'):
        alt=[{**r,'forecasts':{**r['forecasts'],'neighbor':r['forecasts'][name]}} for r in ledger]
        pairs[name]=b.paired(alt)
        metrics[name]['changes']={k:sum(r['forecasts'][name]['pick']!=r['forecasts']['market']['pick'] and ((r['forecasts'][name]['hit'],r['forecasts']['market']['hit'])==v) for r in ledger) for k,v in {'recovered':(True,False),'damaged':(False,True),'both_wrong':(False,False)}.items()}
    return {'selected':chosen or 'market','validation_n':78,'validation_market':vm,'validation_candidates':vr,'metrics':metrics,'paired_vs_market':pairs},ledger

def main():
    report,ledger=build(b.read())
    (ROOT/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8',newline='\n')
    (ROOT/'ledger.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in ledger),encoding='utf8',newline='\n')
    print(json.dumps({'selected':report['selected'],'validation_market':report['validation_market'],'metrics':report['metrics'],'paired':report['paired_vs_market']}))
if __name__=='__main__':main()
