from pathlib import Path
from collections import defaultdict
from datetime import date, timedelta
import hashlib, json
import numpy as np
import upstream
from devig import market_probabilities

HERE=Path(__file__).resolve().parent
NAMES=['all','gap>=0.05','gap>=0.10','gap>=0.20','market_vote_agree','voters>=2','odds<=1.5','odds<=1.8','league=MLB','league=NPB','league=KBO','freshness<=24h']

def feature(e):
    p=market_probabilities(e['prices']); order=e['order']; m=e['picks']['market']; vi=e['vote_counts']; top=max(vi.values()); leaders=[s for s in order if vi.get(s,0)==top]
    return {'id':e['fixture_id'],'date':e['match_date'],'league':e['league'],'market':m,'fixed':e['picks']['market90_vote10'],'p':max(p),'gap':sorted(p,reverse=True)[0]-sorted(p,reverse=True)[1],'agree':len(leaders)==1 and leaders[0]==m,'voters':e['voters'],'odds':e['prices'][order.index(m)],'fixed_odds':e['prices'][order.index(e['picks']['market90_vote10'])]}

def gated(f,i):
    return [True,f['gap']>=.05,f['gap']>=.1,f['gap']>=.2,f['agree'],f['voters']>=2,f['odds']<=1.5,f['odds']<=1.8,f['league']=='MLB',f['league']=='NPB',f['league']=='KBO',False][i]

def score(e, pick, odds):
    return (int(pick==e['winner']), (odds-1 if pick==e['winner'] else -1))

def alt(e,pick,odds):
    # Independent payout via gross return; no upstream result or score calls.
    gross = odds if e['winner']==pick else 0.0
    return (int(bool(gross)),gross-1.0)

def rank_idx(fs,n):
    return set(sorted(range(len(fs)),key=lambda j:(-fs[j]['p'],fs[j]['league'],fs[j]['id']))[:n])

def metrics(es,fs,inds,model='market'):
    pairs=[score(es[j],fs[j][model],fs[j]['odds' if model=='market' else 'fixed_odds']) for j in inds]
    other=[alt(es[j],fs[j][model],fs[j]['odds' if model=='market' else 'fixed_odds']) for j in inds]
    assert pairs == other
    assert (sum(a for a,b in pairs), sum(round(b*100) for a,b in pairs)) == (sum(a for a,b in other), sum(round(b*100) for a,b in other))
    return {'n':len(pairs),'wins':sum(x[0] for x in pairs),'net':sum(x[1] for x in pairs),'accuracy':sum(x[0] for x in pairs)/len(pairs) if pairs else None,'roi':sum(x[1] for x in pairs)/len(pairs) if pairs else None}

def run(rows):
    ev,_,_=upstream.events_from_rows(rows)
    ev=[e for e in ev if all(upstream.result(e,m) not in (None,'push') for m in upstream.MODELS)]
    ev.sort(key=lambda e:(e['match_date'],e['league'],e['fixture_id']))
    days=sorted({e['match_date'] for e in ev}); cut1=days[len(days)//2]; cut2=days[3*len(days)//4]
    parts=[[e for e in ev if cond(e['match_date'])] for cond in [lambda d:d<cut1,lambda d:cut1<=d<cut2,lambda d:d>=cut2]]
    feats=[[feature(e) for e in part] for part in parts]
    scores=[]; eligible=[]
    for i in range(11):
        inds=[{j for j,f in enumerate(fs) if gated(f,i)} for fs in feats[:2]]
        if all(len(ix)/len(fs)>=.4 for ix,fs in zip(inds,feats[:2])):
            va=metrics(parts[1],feats[1],inds[1]); ref=metrics(parts[1],feats[1],rank_idx(feats[1],len(inds[1])))
            eligible.append(i); scores.append((va['accuracy']-ref['accuracy'],-i))
    chosen=-max(scores)[1] if scores else None
    e=parts[2]; f=feats[2]; inds={j for j,x in enumerate(f) if gated(x,chosen)}; rank=rank_idx(f,len(inds)); allidx=set(range(len(e)))
    weeks=sorted({(date.fromisoformat(x['date'])-timedelta(days=date.fromisoformat(x['date']).weekday())).isoformat() for x in f})
    matrix=np.zeros((len(weeks),6)); wk={w:i for i,w in enumerate(weeks)}
    for j,x in enumerate(f):
        w=(date.fromisoformat(x['date'])-timedelta(days=date.fromisoformat(x['date']).weekday())).isoformat(); z=matrix[wk[w]]
        a,r=score(e[j],x['market'],x['odds']); b,s=score(e[j],x['fixed'],x['fixed_odds'])
        if j in inds: z += [a,r,1,b,s,0]
        if j in rank: z += [0,0,0,0,0,1]
    # Aggregate comparable rank numerators independently on same opportunity weeks.
    refmat=np.zeros((len(weeks),2))
    for j in rank:
        x=f[j]; w=(date.fromisoformat(x['date'])-timedelta(days=date.fromisoformat(x['date']).weekday())).isoformat(); refmat[wk[w]]+=score(e[j],x['market'],x['odds'])
    rng=np.random.default_rng(20261030); draws=rng.integers(len(weeks),size=(20000,len(weeks))); sm=matrix[draws].sum(axis=1); rf=refmat[draws].sum(axis=1); mask=(sm[:,2]>0)&(sm[:,5]>0); sm=sm[mask]; rf=rf[mask]
    ds={'rank_accuracy':sm[:,0]/sm[:,2]-rf[:,0]/sm[:,5],'rank_roi':sm[:,1]/sm[:,2]-rf[:,1]/sm[:,5], 'fixed_accuracy':(sm[:,0]-sm[:,3])/sm[:,2], 'fixed_roi':(sm[:,1]-sm[:,4])/sm[:,2]}
    ci={k:{'95':np.quantile(v,[.025,.975]).tolist(),'bonferroni_12':np.quantile(v,[.05/(2*12),1-.05/(2*12)]).tolist()} for k,v in ds.items()}
    curve=[]
    for i in range(11):
        ix={j for j,x in enumerate(f) if gated(x,i)}; curve.append({'candidate':NAMES[i],'coverage':len(ix)/len(f),'gate':metrics(e,f,ix),'rank':metrics(e,f,rank_idx(f,len(ix)))})
    return {'count':len(ev),'split':[{'n':len(p),'from':p[0]['match_date'],'to':p[-1]['match_date']} for p in parts], 'eligible':[NAMES[i] for i in eligible],'validation_scores':scores,'selected':NAMES[chosen],'test_coverage':len(inds)/len(e),'test_gate':metrics(e,f,inds),'test_fixed_same':metrics(e,f,inds,'fixed'),'test_market_full':metrics(e,f,allidx),'test_rank':metrics(e,f,rank),'ci':ci,'test_weeks':len(weeks),'curves':curve,'prediction_fingerprint':hashlib.sha256(json.dumps(feats,sort_keys=True).encode()).hexdigest()}

if __name__=='__main__':
    rows=[json.loads(x) for x in (HERE/'input.jsonl').read_text(encoding='utf8').splitlines()]; result=run(rows)
    flipped=[dict(r,home_score=r['away_score'],away_score=r['home_score']) if r['match_date']>=result['split'][2]['from'] else r for r in rows]
    check=run(flipped); assert result['selected']==check['selected'] and result['prediction_fingerprint']==check['prediction_fingerprint']
    result['label_flip_check']='passed'; result['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [HERE/'input.jsonl',HERE/'run.py',HERE/'upstream.py',HERE/'devig.py']}
    (HERE/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8'); print(json.dumps({k:result[k] for k in ('count','split','eligible','selected','test_coverage','test_gate','test_rank','test_fixed_same','ci','label_flip_check')},ensure_ascii=False))
