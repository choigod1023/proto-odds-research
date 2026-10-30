import collections, datetime as dt, gzip, hashlib, json, math, pathlib, urllib.parse
import numpy as np
from scipy.stats import beta

ROOT = pathlib.Path(__file__).resolve().parent
PRIORS = (1, 5, 10)
def read(name):
    with gzip.open(ROOT / 'inputs' / name, 'rt', encoding='utf8') as f:
        return [json.loads(s) for s in f]
def ts(s):
    try:
        d = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (ValueError, TypeError, AttributeError): return None
def norm(s): return str(s or '').strip().casefold()
def url(s):
    u = urllib.parse.urlsplit(s or '')
    return (u.netloc.casefold() + u.path.rstrip('/').casefold()) if u.netloc else ''
def keyauthor(x):
    a = norm(x.get('author')); site = norm(x.get('source_site') or urllib.parse.urlsplit(x.get('source_url') or '').netloc or x.get('source'))
    return (site, a) if site and a else None

def prepare(frames, raw):
    # Identity links are formed before any time or eligibility filter.
    allrows = [dict(x) for x in raw]
    for f in frames:
        for x in f['raw_forecasts']:
            allrows.append(dict(x, event_id=f['event_id'], kickoff=f['kickoff'], selection=x.get('side')))
    parent = list(range(len(allrows))); tokens = {}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(i,j):
        a,b=find(i),find(j)
        if a != b: parent[max(a,b)] = min(a,b)
    for i,x in enumerate(allrows):
        author=keyauthor(x); u=url(x.get('canonical_article_url') or x.get('source_url'))
        keys=[]
        if author: keys.append(('author',author))
        if u: keys.append(('url',u))
        # Source-file hashes are not article hashes and cannot prove copying.
        for field in ('article_body_sha256',):
            if x.get(field): keys.append(('body',x[field]))
        if x.get('document_hash_kind') in ('article_dom_utf8','original_input_document_sha256') and x.get('document_sha256'): keys.append(('doc',x['document_sha256']))
        for field in ('citation_urls','cited_urls','cross_citations'):
            values=x.get(field) or []
            if isinstance(values,str): values=[values]
            for v in values:
                if isinstance(v,str) and url(v): keys.append(('url',url(v)))
        for k in keys:
            if k in tokens: union(i,tokens[k])
            else: tokens[k]=i
    grouped=collections.defaultdict(list); reasons=collections.Counter()
    for i,x in enumerate(allrows):
        if not keyauthor(x): reasons['missing_author']+=1; continue
        when=ts(x.get('published_at_claim') or x.get('source_reported_published_at')); kickoff=ts(x.get('kickoff'))
        if not when or not kickoff: reasons['missing_timestamp']+=1; continue
        if when >= kickoff: reasons['postgame_claim']+=1; continue
        if x.get('selection') not in ('home','away','draw'): reasons['missing_selection']+=1; continue
        grouped[(x.get('event_id'),find(i))].append((when,x['selection']))
    eventvotes=collections.defaultdict(list)
    for (event,group),items in grouped.items():
        # Conflicting recommendations by the same component are excluded.
        if len({s for _,s in items})>1: reasons['conflicting_component']+=1; continue
        eventvotes[event].append((str(group),min(items)[1]))
    eligible=[]
    for f in frames:
        # The fixed baseline was calculated from the original frame's votes. If
        # any of those were posted after kickoff, discard the entire frame.
        kickoff=ts(f['kickoff'])
        if any(ts(z.get('source_reported_published_at')) is None for z in f['raw_forecasts']):
            reasons['frame_unknown_publication_excluded']+=1; continue
        if any((t:=ts(z.get('source_reported_published_at'))) is not None and t>=kickoff for z in f['raw_forecasts']):
            reasons['frame_postgame_excluded']+=1; continue
        choices=f['choices']; sides=[x['side'] for x in choices]
        votes=[(a,s) for a,s in eventvotes[f['event_id']] if s in sides]
        if not votes or f.get('source_plurality_tie') or f.get('winner') not in sides: continue
        if any(not isinstance(x.get('odds'),(int,float)) or x['odds']<=1 for x in choices): continue
        eligible.append({'id':f['event_id'],'kickoff':f['kickoff'],'winner':f['winner'],'sides':sides,'odds':[x['odds'] for x in choices], 'market':[x['scores']['market'] for x in choices], 'fixed':f['original_model_picks']['market90_vote10'], 'votes':sorted(votes)})
    eligible.sort(key=lambda x:(ts(x['kickoff']),x['id']))
    names=collections.defaultdict(set)
    for i,x in enumerate(allrows):
        if keyauthor(x): names[str(find(i))].add(' / '.join(keyauthor(x)))
    return eligible, dict(reasons), len(allrows), {k:sorted(v) for k,v in names.items()}

def split(rows):
    # Whole dates belong to one period.
    days=sorted(set(ts(x['kickoff']).date() for x in rows)); n=len(rows)
    counts=collections.Counter(ts(x['kickoff']).date() for x in rows); total=0; a=b=None
    for day in days:
        if a is None and total>=n*.5: a=day
        if b is None and total>=n*.75: b=day; break
        total+=counts[day]
    return [[x for x in rows if (ts(x['kickoff']).date()<a if i==0 else a<=ts(x['kickoff']).date()<b if i==1 else ts(x['kickoff']).date()>=b)] for i in range(3)]
def fit(train, boundary):
    history=collections.defaultdict(lambda:[0,0])
    for x in train:
        t=ts(x['kickoff'])
        if t.date() >= boundary.date() or t+dt.timedelta(hours=48)>boundary: continue
        for author,side in x['votes']:
            history[author][1]+=1; history[author][0]+=int(side==x['winner'])
    return dict(sorted(history.items()))
def predict(x,history,prior):
    weights=collections.defaultdict(float)
    for author,side in x['votes']:
        h,n=history.get(author,(0,0)); weights[side]+=(h+prior)/(n+2*prior)
    total=sum(weights.values()); vals=[round(.9*p+.1*weights[s]/total,4) for s,p in zip(x['sides'],x['market'])]
    return x['sides'][max(range(len(vals)),key=lambda i: vals[i])]
def score(rows, history, prior):
    out=[]
    for x in rows:
        picks={'weighted':predict(x,history,prior), 'market':x['sides'][max(range(len(x['sides'])),key=lambda i:x['market'][i])], 'fixed':x['fixed']}
        if any(p not in x['sides'] for p in picks.values()): raise ValueError('invalid selection')
        out.append(dict(x,picks=picks))
    return out
def ledger(rows):
    out=[]
    for x in rows:
        item={'id':x['id'],'week':str(ts(x['kickoff']).date()-dt.timedelta(days=ts(x['kickoff']).weekday())), 'winner':x['winner'],'sides':x['sides'],'odds':x['odds'],'picks':x['picks']}
        item['settlement']={k:{'hit':int(v==x['winner']),'profit_cents':(round(100*x['odds'][x['sides'].index(v)]) if v==x['winner'] else 0)-100} for k,v in x['picks'].items()}
        out.append(item)
    return out
def metric(rows):
    return {k:{'hit':sum(x['settlement'][k]['hit'] for x in rows),'n':len(rows),'accuracy':sum(x['settlement'][k]['hit'] for x in rows)/len(rows) if rows else None,'roi':sum(x['settlement'][k]['profit_cents'] for x in rows)/(100*len(rows)) if rows else None} for k in ('weighted','market','fixed')}
def ci(rows):
    weeks=sorted({x['week'] for x in rows}); grouped={w:[x for x in rows if x['week']==w] for w in weeks}; rng=np.random.default_rng(20261030); vals=collections.defaultdict(list)
    for _ in range(2000):
        sample=[x for w in rng.choice(weeks,size=len(weeks),replace=True) for x in grouped[w]]
        for base in ('market','fixed'):
            for field in ('hit','profit'):
                source='profit_cents' if field=='profit' else field
                vals[base+'_'+field].append(sum(x['settlement']['weighted'][source]-x['settlement'][base][source] for x in sample)/(len(sample)*(100 if field=='profit' else 1)))
    return {k:{'95':np.quantile(v,[.025,.975]).tolist(),'bonferroni_3':np.quantile(v,[.05/6,1-.05/6]).tolist()} for k,v in vals.items()}
def hashfile(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    raw=read('old-votes-raw.jsonl.gz')+read('new-votes-raw.jsonl.gz'); frames=read('frames.jsonl.gz')
    rows,reasons,raw_n,names=prepare(frames,raw); train,val,test=split(rows); boundary=min(ts(x['kickoff']) for x in val); history=fit(train,boundary)
    val_scores=[metric(ledger(score(val,history,p)))['weighted']['accuracy'] for p in PRIORS]; selected=PRIORS[max(range(len(PRIORS)),key=lambda i:val_scores[i])]
    led=ledger(score(test,history,selected)); (ROOT/'ledger.json').write_text(json.dumps(led,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    author={a:{'identity':names.get(a,[]),'hits':h,'n':n,'mean':(h+selected)/(n+2*selected),'interval_95':beta.ppf([.025,.975],h+selected,n-h+selected).tolist()} for a,(h,n) in history.items()}
    candidates={str(p):{'metrics':metric(z:=ledger(score(test,history,p))),'ci':ci(z)} for p in PRIORS}
    disagreements=[x for x in led if x['picks']['market']!=x['picks']['weighted']]
    result={'scope':'retrospective; receipt and settlement clocks not authenticated','strict_certified_n':0,'raw_vote_rows':len(raw),'frame_rows':len(frames),'all_identity_rows':raw_n,'filters':reasons,'eligible_common':len(rows),'splits':{name:{'n':len(xs),'first':xs[0]['kickoff'],'last':xs[-1]['kickoff']} for name,xs in zip(('train','validation','test'),(train,val,test))},'freeze_at':boundary.isoformat(),'validation_accuracy':dict(zip(map(str,PRIORS),val_scores)),'selected_prior':selected,'independent_vote_count':dict(sorted(collections.Counter(str(len(x['votes'])) for x in rows).items())),'test_vote_count':dict(sorted(collections.Counter(str(len(x['votes'])) for x in test).items())),'authors':author,'candidate_test_results':candidates,'selected_test':metric(led),'disagreement':metric(disagreements),'disagreement_ci':ci(disagreements) if disagreements else None}
    (ROOT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8',newline='\n')
    return result
if __name__=='__main__': main()
