"""Pre-registered retrospective expert residual experiment, offline only."""
import collections
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parent
TRAIN_END = datetime(2026, 6, 1, tzinfo=timezone.utc)
TEST_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
TEST_END = datetime(2026, 11, 1, tzinfo=timezone.utc)


def instant(s):
    d = datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None: raise ValueError('naive time')
    return d.astimezone(timezone.utc)


def read_gz(path):
    with gzip.open(path,'rt',encoding='utf-8') as f: return [json.loads(line) for line in f]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def groups(votes, decision, sides):
    by_author = collections.defaultdict(list)
    for v in votes:
        try:
            source, author, side = v['source'],v['author'],v['side']
            if not isinstance(source,str) or not source.strip() or not isinstance(author,str) or not author.strip() or side not in sides: continue
            published = instant(v['source_reported_published_at'])
            if published > decision: continue
        except (KeyError,ValueError,TypeError,AttributeError): continue
        h = v.get('document_sha256') or v.get('source_sha256')
        if h is not None and (not isinstance(h,str) or not re.fullmatch('[0-9a-fA-F]{64}', h)): h = None
        by_author[(source,author)].append((published, side, h.lower() if h else None))
    docs = collections.defaultdict(list)
    for key, entries in sorted(by_author.items()):
        if len({e[1] for e in entries}) != 1: continue
        _, side, doc = sorted(entries,key=lambda x:(x[0],x[2] or ''))[0]
        docs[('sha',doc) if doc else ('author',*key)].append((key[0],key[1],side))
    return [sorted(set(v)) for _,v in sorted(docs.items())]


def eligible(targets, frames, cases):
    frame_map=collections.defaultdict(list)
    for f in frames: frame_map[f.get('event_id')].append(f)
    case_map=collections.defaultdict(list)
    for c in cases: case_map[(c.get('cohort'),c.get('league'),c.get('event_id'))].append(c)
    target_counts=collections.Counter(t.get('event_id') for t in targets if t.get('actual_votes') is True)
    out=[]; rejected=collections.Counter(); vote_audit=collections.Counter()
    for t in targets:
        if t.get('actual_votes') is not True: continue
        try:
            eid=t['event_id']; frows=frame_map[eid]
            cs=case_map[(t['cohort'],t['league'],eid)]
            if target_counts[eid]!=1 or len(frows)!=1 or len(cs)!=1: raise ValueError('missing_or_duplicate_event')
            f=frows[0]; c=cs[0]
            if t['cohort']!='retrospective_year_strict__claimed_time_only' or f.get('cohort')!='retrospective_year_strict': raise ValueError('cohort_mismatch')
            bridge=t['identity_bridge']
            if (t['league']!=c['league'] or t['league']!=f['league'] or bridge['official_fixture_id']!=eid
                or bridge['canonical_names']!=[t['home'],t['away']] or bridge['source_names']!=[c['home'],c['away']]
                or [f['home'],f['away']]!=[c['home'],c['away']] or instant(t['kickoff'])!=instant(c['kickoff'])
                or instant(t['kickoff'])!=instant(f['kickoff']) or t['sides']!=c['sides']
                or t['odds']!=c['odds'] or t['market_p']!=c['market_p'] or t['decision_at']!=c['decision_at'] or c.get('void')):
                raise ValueError('identity_product_price_mismatch')
            decision=instant(t['decision_at']); kickoff=instant(t['kickoff'])
            if decision > kickoff-timedelta(minutes=30): raise ValueError('decision_after_t30')
            if len(frows)!=1 or target_counts[eid]!=1: raise ValueError('ambiguous_event')
            f=frows[0]; bridge=t['identity_bridge']; sides=t['sides']; p=t['market_p']; odds=t['odds']
            if f['league']!=t['league'] or f['event_id']!=eid or bridge['official_fixture_id']!=eid or bridge['source_names']!=[f['home'],f['away']] or bridge['canonical_names']!=[t['home'],t['away']] or instant(f['kickoff'])!=instant(t['kickoff']): raise ValueError('identity')
            decision=instant(t['decision_at']); kickoff=instant(t['kickoff'])
            if decision>kickoff-timedelta(minutes=30): raise ValueError('decision_time')
            if sides not in (['home','away'],['home','draw','away']) or len(p)!=len(sides) or len(odds)!=len(sides) or any(not isinstance(z,(float,int)) or isinstance(z,bool) or not math.isfinite(z) or z<=0 for z in p) or abs(sum(p)-1)>1e-8 or any(not isinstance(z,(float,int)) or isinstance(z,bool) or not math.isfinite(z) or z<=1 for z in odds): raise ValueError('market_vector')
            choices=[x for x in f['choices'] if [x.get('issue_year'),x.get('round')]==c['issue'] and str(x.get('game_no'))==str(c['game_no'])]
            if len(choices)!=len(sides) or [c['side'] for c in choices]!=sides or any(c['market_family'] not in ('moneyline','1x2') or c['line'] is not None or not c.get('exact_offer_coordinates_saved') or not math.isclose(float(c['odds']),float(o),rel_tol=0,abs_tol=1e-9) for c,o in zip(choices,odds)): raise ValueError('market_identity')
            if not choices[0].get('offer_id') or len({x.get('offer_id') for x in choices})!=1 or len({x.get('market') for x in choices})!=1 or any(not math.isclose(float(x['scores']['market']),float(m),rel_tol=0,abs_tol=1e-12) for x,m in zip(choices,p)): raise ValueError('market_identity')
            y=t['outcome']
            if type(y) is not int or not 0<=y<len(sides) or f.get('result_evidence')!='official_exact_fixture' or not f.get('result_known') or f.get('winner')!=sides[y] or c.get('outcome')!=y: raise ValueError('official_result')
            gs=groups(f['raw_forecasts'],decision,sides)
            if not gs: raise ValueError('no_eligible_votes')
            vote_audit['raw_votes']+=len(f['raw_forecasts']);vote_audit['groups']+=len(gs);vote_audit['kept_authors']+=sum(len(g) for g in gs)
            features={'event_id':eid,'league':t['league'],'decision_at':t['decision_at'],'kickoff':t['kickoff'],'sides':sides,'market_p':p,'odds':odds,'groups':gs,'previous_fixed_pick':t.get('fixed_pick')}
            out.append({'features':features,'outcome':y,'available_at':(kickoff+timedelta(hours=96)).isoformat()})
        except (KeyError,TypeError,ValueError,AttributeError,OverflowError) as ex: rejected[str(ex)]+=1
    return sorted(out,key=lambda r:(instant(r['features']['decision_at']),r['features']['event_id'])),dict(rejected),dict(vote_audit)


def indices(rows, hierarchical):
    leagues=sorted({r['features']['league'] for r in rows}) if hierarchical else []
    authors=sorted({(r['features']['league'],s,a) for r in rows for g in r['features']['groups'] for s,a,_ in g}) if hierarchical else []
    keys=[('global',)] + [('league',l) for l in leagues] + [('author',*a) for a in authors]
    return {k:i for i,k in enumerate(keys)}


def design(f, ix):
    sides=f['sides']; market=np.array(f['market_p'],float); x=np.zeros((len(sides),len(ix)))
    for group in f['groups']:
        for source,author,side in group:
            z=-market.copy(); z[sides.index(side)]+=1
            for key in (('global',),('league',f['league']),('author',f['league'],source,author)):
                if key in ix: x[:,ix[key]] += z/(len(f['groups'])*len(group))
    return x


def fit(rows, kind):
    if not rows: return {'kind':kind,'index':{},'coef':[],'n':0,'success':False,'reason':'no_training_data'}
    ix=indices(rows,kind=='expert_hierarchical')
    weights=np.array([1 if k[0]=='global' else 4 if k[0]=='league' else 16 for k in ix]); bounds=[(-2,2) if k[0]=='global' else (-1,1) for k in ix]
    x=np.zeros((len(rows),3,len(ix))); offsets=np.full((len(rows),3),-np.inf); ys=[]
    for i,row in enumerate(rows):
        f=row['features']; k=len(f['sides']); x[i,:k,:]=design(f,ix); offsets[i,:k]=np.log(f['market_p']); ys.append(row['outcome'])
    ii=np.arange(len(rows)); ys=np.array(ys,dtype=int)
    def obj(b):
        z=offsets+np.einsum('nkd,d->nk',x,b); norm=logsumexp(z,axis=1)
        loss=.5*np.dot(weights*b,b)+np.sum(norm-z[ii,ys])
        pp=np.exp(z-norm[:,None]); pp[ii,ys]-=1
        return loss,weights*b+np.einsum('nkd,nk->d',x,pp)
    r=minimize(obj,np.zeros(len(ix)),jac=True,method='L-BFGS-B',bounds=bounds,options={'maxiter':500,'ftol':1e-12})
    if not r.success or not np.all(np.isfinite(r.x)): raise RuntimeError('fit failed: '+str(r.message))
    return {'kind':kind,'index':ix,'coef':r.x.tolist(),'n':len(rows),'success':True,'objective':float(r.fun),'iterations':int(r.nit)}


def predict(model, features):
    if set(features) != {'event_id','league','decision_at','kickoff','sides','market_p','odds','groups','previous_fixed_pick'}: raise ValueError('unsupported or result-bearing feature')
    market=np.array(features['market_p'],float)
    if model['kind']=='market': return market.tolist()
    if not model['success']: raise ValueError('unfitted')
    contribution=design(features,model['index'])@np.array(model['coef'])
    if np.all(contribution==0): return market.tolist()
    z=np.log(market)+contribution; p=np.exp(z-logsumexp(z))
    return p.tolist()


def prior_rows(rows, cutoff, inclusive=False):
    return [r for r in rows if (instant(r['available_at']) <= cutoff if inclusive else instant(r['available_at']) < cutoff) and instant(r['features']['decision_at']) < cutoff]


def predictions(rows):
    initial=prior_rows(rows,TRAIN_END)
    validation=[r for r in rows if TRAIN_END<=instant(r['features']['decision_at'])<TEST_START and instant(r['available_at'])<TEST_START]
    if len(validation)<30: raise ValueError('validation events below preregistered minimum 30')
    candidates=('market','expert_global','expert_hierarchical')
    val_models={'market':{'kind':'market'}, **{kind:fit(initial,kind) for kind in candidates[1:]}}
    losses={}
    for kind in candidates:
        losses[kind]=sum(-math.log(predict(val_models[kind],r['features'])[r['outcome']]) for r in validation)/len(validation)
    best=min(losses.values()); selected=next(k for k in candidates if losses[k]<=best+1e-12)
    train=prior_rows(rows,TEST_START)
    frozen={'kind':'market'} if selected=='market' else fit(train,selected)
    sham_rows=copy.deepcopy(rows)
    for r in sham_rows:
        f=r['features']; favourite=f['sides'][int(np.argmax(f['market_p']))]
        f['groups']=[[(source,author,favourite) for source,author,_ in group] for group in f['groups']]
    sham_frozen={'kind':'market'} if selected=='market' else fit(prior_rows(sham_rows,TEST_START),selected)
    sham_by_id={r['features']['event_id']:r for r in sham_rows}
    forecasts=[]; cache={}
    for r in rows:
        f=r['features']; at=instant(f['decision_at'])
        if not TEST_START<=at<TEST_END: continue
        day=at.replace(hour=0,minute=0,second=0,microsecond=0)
        if day not in cache:
            actual={'kind':'market'} if selected=='market' else fit(prior_rows(rows,day,inclusive=True),selected)
            sham={'kind':'market'} if selected=='market' else fit(prior_rows(sham_rows,day,inclusive=True),selected)
            cache[day]=(actual,sham)
        for mode,model,features,name in (
            ('frozen',frozen,f,'selected'),('prequential',cache[day][0],f,'selected'),
            ('frozen',sham_frozen,sham_by_id[f['event_id']]['features'],'sham'),
            ('prequential',cache[day][1],sham_by_id[f['event_id']]['features'],'sham'),
            ('frozen',{'kind':'market'},f,'market'),('prequential',{'kind':'market'},f,'market')):
            probs=predict(model,features)
            forecasts.append({'event_id':f['event_id'],'league':f['league'],'decision_at':f['decision_at'],'mode':mode,'model':name,'probabilities':probs,'pick':int(np.argmax(probs)),'training_n':model.get('n',0)})
        group_p=np.zeros(len(f['sides']))
        counts=[0]*len(f['sides'])
        for group in f['groups']:
            for _,_,side in group:
                i=f['sides'].index(side);group_p[i]+=1/(len(f['groups'])*len(group));counts[i]+=1
        recomputed=[.9*p+.1*v for p,v in zip(f['market_p'],group_p)]
        rounded=[round(v,4) for v in recomputed]
        forecasts.append({'event_id':f['event_id'],'league':f['league'],'decision_at':f['decision_at'],'mode':'comparison','model':'fixed91_recomputed','probabilities':recomputed,'pick':int(np.argmax(recomputed)),'training_n':None})
        forecasts.append({'event_id':f['event_id'],'league':f['league'],'decision_at':f['decision_at'],'mode':'comparison','model':'fixed91_recomputed_rounded4','probabilities':rounded,'pick':int(np.argmax(rounded)),'training_n':None})
        if f['previous_fixed_pick'] in f['sides']:
            forecasts.append({'event_id':f['event_id'],'league':f['league'],'decision_at':f['decision_at'],'mode':'comparison','model':'prior_research_fixed91_pick','probabilities':None,'pick':f['sides'].index(f['previous_fixed_pick']),'training_n':None})
        if counts.count(max(counts))==1:
            forecasts.append({'event_id':f['event_id'],'league':f['league'],'decision_at':f['decision_at'],'mode':'comparison','model':'author_plurality','probabilities':None,'pick':int(np.argmax(counts)),'training_n':None})
    return forecasts,{'validation_n':len(validation),'validation_excluded_not_settled_by_july1':sum(1 for r in rows if TRAIN_END<=instant(r['features']['decision_at'])<TEST_START and instant(r['available_at'])>=TEST_START),'validation_logloss':losses,'selected':selected,'train_n_before_june':len(initial),'frozen_n_before_july':len(train),'prequential_utc_days':len(cache),'selection_note':'selected by validation before test; no per-league reselection; sham is selected architecture only'}


def measure(forecasts, rows):
    lookup={r['features']['event_id']:r for r in rows}
    market={(x['event_id'],x['mode']):x['pick'] for x in forecasts if x['model']=='market'}
    aggregate=collections.defaultdict(list); exact=[]
    for x in forecasts:
        r=lookup[x['event_id']]; f=r['features']; y=r['outcome']; pick=x['pick']
        hit=pick==y; profit=(Decimal(str(f['odds'][pick]))-Decimal(1)) if hit else Decimal(-1)
        base=market[(x['event_id'], x['mode'] if x['mode'] != 'comparison' else 'frozen')]
        base_hit=base==y; base_profit=(Decimal(str(f['odds'][base]))-Decimal(1)) if base_hit else Decimal(-1)
        item={'event_id':x['event_id'],'hit':hit,'profit':str(profit),'market_hit':base_hit,'market_profit':str(base_profit),'changed':pick!=base,'week':instant(x['decision_at']).strftime('%G-W%V'), 'day':instant(x['decision_at']).date().isoformat(), 'probabilities':x['probabilities'], 'outcome':y}
        exact.append(dict(x, outcome=y, profit=str(profit)))
        split='validation' if instant(x['decision_at'])<TEST_START else 'test'
        for league in (x['league'],'ALL'): aggregate[(split,league,x['mode'],x['model'])].append(item)
    results=[]
    for (split,league,mode,model),items in sorted(aggregate.items()):
        n=len(items); wins=sum(int(x['hit']) for x in items); profit=sum((Decimal(x['profit']) for x in items),Decimal(0))
        grouped=collections.defaultdict(list)
        for item in items: grouped[item['week']].append(item)
        weeks=sorted(grouped); ci_hit=None; ci_roi=None
        if len(weeks)>=2:
            rng=np.random.default_rng(20261031); diffs=[]
            for _ in range(5000):
                sample=[v for j in rng.integers(len(weeks),size=len(weeks)) for v in grouped[weeks[int(j)]]]
                diffs.append((sum(int(z['hit'])-int(z['market_hit']) for z in sample)/len(sample), sum(float(Decimal(z['profit'])-Decimal(z['market_profit'])) for z in sample)/len(sample)))
            ci_hit=np.quantile(np.array(diffs)[:,0],[.025,.975]).tolist(); ci_roi=np.quantile(np.array(diffs)[:,1],[.025,.975]).tolist()
        eligible_n=sum(1 for r in rows if TEST_START<=instant(r['features']['decision_at'])<TEST_END and (league=='ALL' or r['features']['league']==league))
        probability_rows=[x for x in items if x['probabilities'] is not None]
        logloss=None; brier=None
        if probability_rows:
            logloss=sum(-math.log(max(1e-15,x['probabilities'][x['outcome']])) for x in probability_rows)/len(probability_rows)
            brier=sum(sum((p-(i==x['outcome']))**2 for i,p in enumerate(x['probabilities'])) for x in probability_rows)/len(probability_rows)
        daily=collections.defaultdict(Decimal); monthly=collections.defaultdict(Decimal)
        for item in items:
            val=Decimal(item['profit']); daily[item['day']]+=val; monthly[item['day'][:7]]+=val
        balance=Decimal(0); peak=Decimal(0); max_dd=Decimal(0)
        for day in sorted(daily):
            balance+=daily[day];peak=max(peak,balance);max_dd=max(max_dd,peak-balance)
        results.append({'split':split,'league':league,'mode':mode,'model':model,'n':n,'hits':wins,'accuracy':wins/n,'roi':float(profit/Decimal(n)),'coverage_on_eligible':n/eligible_n if eligible_n else None,'eligible_denominator':eligible_n,'logloss':logloss,'brier':brier,'monthly_unit_pnl':{k:str(v) for k,v in sorted(monthly.items())},'daily_close_max_drawdown_units':str(max_dd),'descriptive_n_under_30':n<30,'pick_changes_vs_market':sum(x['changed'] for x in items),'observed_calendar_weeks':len(weeks),'paired_weekly_95ci_accuracy_vs_market':ci_hit,'paired_weekly_95ci_roi_vs_market':ci_roi})
    return results, exact


def run():
    inp=ROOT/'inputs'; p=json.loads((inp/'provenance.json').read_text(encoding='utf-8'))
    if sha(inp/'votes.jsonl.gz')!=p['sources']['raw_frames']['sha256']: raise ValueError('raw_frames hash mismatch')
    if sha(inp/'cases.jsonl.gz')!=p['sources']['cases']['sha256']: raise ValueError('cases hash mismatch')
    rows,rejected,audit=eligible(read_gz(inp/'targets.jsonl.gz'),read_gz(inp/'votes.jsonl.gz'),read_gz(inp/'cases.jsonl.gz'))
    forecasts,stats=predictions(rows)
    results,settlement=measure(forecasts,rows)
    split=collections.Counter('train' if instant(r['features']['decision_at'])<TRAIN_END else 'validation' if instant(r['features']['decision_at'])<TEST_START else 'test' if instant(r['features']['decision_at'])<TEST_END else 'outside' for r in rows)
    output={'protocol_commit':'a1ed59f8849a29b753fa9a45df482ad294ed5862','inputs':{x.name:sha(x) for x in inp.iterdir() if x.is_file()},'eligible':len(rows),'split_by_decision':dict(split),'rejected':rejected,'vote_audit':audit,'fit':stats,'metrics':results,'limitations':['retrospective publication claims only; certified receipt and executable prices zero','fixed91 is recomputed from grouped directional frequencies, not frozen operating predictions','96h result availability is a research assumption','previously inspected data; not blind or prospective']}
    (ROOT/'results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'forecasts.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in forecasts),encoding='utf-8')
    (ROOT/'settlements.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in settlement),encoding='utf-8')
    print(json.dumps({'eligible':len(rows),'split':dict(split),'rejected':rejected,'fit':stats},ensure_ascii=False))

if __name__=='__main__': run()
