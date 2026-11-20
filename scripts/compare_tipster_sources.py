"""Fixed candidate, source-stratified retrospective comparison; no production writes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
from pathlib import Path
import sys
from urllib.parse import urlsplit
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sport_hybrid_validation import load_data
from matches import load_matches
from audit_tipster_expansion import fixture_key, article_date, identity_candidates, parse_tip

ARMS = ('tipster', 'market90_tipster10')


def source_tip(a,r,aliases):
    if urlsplit(a['url']).netloc.removeprefix('www.') != 'pickdawgz.com':
        return parse_tip(a,r['home'],r['away'],aliases,r['sport'])
    # Read the explicit author pick in the preserved English body, not a translated label.
    text=a.get('native_pick','')
    if not text or text not in a['body']:
        return None
    match=re.fullmatch(r".+?['’]s Pick:\s*(.+?)(?:\s+[+-]\d{3,4})?",text)
    if not match or re.search(r'over|under|\d|run line',match[1],re.I):
        return None
    clean=lambda s:re.sub(r'[^a-z]','',s.lower())
    label=clean(match[1])
    sides=[i for i,s in enumerate(('home','away')) if clean(r['raw_team_names'][s]).startswith(label)]
    return sides[0] if len(sides)==1 and len(label)>=4 else None


def replay(rows, arm, limit=True):
    monthly = defaultdict(int)
    days = defaultdict(list)
    bets = []
    curve = [0]
    for r in rows:
        days[r['date']].append(r)
    for day, batch in sorted(days.items()):
        month = day[:7]
        chosen = []
        for r in sorted(batch, key=lambda x: (x['kickoff'], fixture_key(x))):
            if not limit or monthly[month] - 10000*(len(chosen)+1) >= -50000:
                chosen.append(r)
        net = 0
        for r in chosen:
            pick = r['picks'][arm]
            profit = round(10000*(r['odds'][pick]*(pick == r['winner'])-1))
            net += profit
            bets.append({'fixture': list(fixture_key(r)), 'pick':pick, 'profit_krw':profit})
        monthly[month] += net
        curve.append(curve[-1]+net)
    values = list(monthly.values())
    if limit:
        assert all(x >= -50000 for x in values)
    return dict(bets=len(bets), profit_krw=sum(values),
                roi_pct=sum(values)/(100*len(bets)) if bets else None,
                monthly_net=dict(monthly), active_months=len(values),
                monthly_mean=float(np.mean(values)) if values else None,
                monthly_median=float(np.median(values)) if values else None,
                monthly_std=float(np.std(values, ddof=1)) if len(values)>1 else None,
                losing_months=sum(x<0 for x in values), loss_ge_40000_months=sum(x<=-40000 for x in values),
                worst_month=min(values) if values else None,
                max_daily_drawdown_krw=int(max(np.maximum.accumulate(curve)-np.array(curve))),
                bet_ledger=bets)


def comparison(rows):
    capped = {a:replay(rows,a) for a in ARMS}
    uncapped = {a:replay(rows,a,False) for a in ARMS}
    months = sorted(set(capped[ARMS[0]]['monthly_net']) | set(capped[ARMS[1]]['monthly_net']))
    diffs = [capped[ARMS[1]]['monthly_net'].get(m,0)-capped[ARMS[0]]['monthly_net'].get(m,0) for m in months]
    interval = None
    # Two observed partial months do not support a useful stability interval.
    if len(diffs)>=6:
        rng = np.random.default_rng(20261026)
        samples = rng.choice(diffs,size=(5000,len(diffs)),replace=True).mean(axis=1)
        interval = np.quantile(samples,[.025,.975]).tolist()
    return dict(n=len(rows), by_sport=dict(Counter(r['sport'] for r in rows)),
                start=min((r['date'] for r in rows),default=None),end=max((r['date'] for r in rows),default=None),
                capped=capped,uncapped=uncapped,
                hybrid_better_months=sum(x>0 for x in diffs),tipster_better_months=sum(x<0 for x in diffs),
                equal_months=sum(x==0 for x in diffs), monthly_hybrid_minus_tipster=dict(zip(months,diffs)),
                interval_policy='descriptive month-block bootstrap only with >=6 observed months; not confirmatory',
                descriptive_monthly_mean_difference_95_interval=interval)


def content_overlap(ledger, articles, previous_articles):
    """Flag copied bodies, not merely equal tips; similarity is not authorship proof."""
    import re
    def shingles(body):
        s=re.sub(r'\s+','',body)
        return {s[i:i+12] for i in range(max(0,len(s)-11))}
    old_by_date=defaultdict(list)
    for a in previous_articles:
        old_by_date[article_date(a['title'])].append(a)
    cache={}
    findings=[]
    for r in ledger:
        a=articles[r['article_url']]
        sa=shingles(a['body'])
        best=(0,None)
        for b in old_by_date[r['date']]:
            if urlsplit(b['url']).netloc.removeprefix('www.')==r['site']:
                continue
            sb=cache.setdefault(b['url'],shingles(b['body']))
            sim=len(sa&sb)/len(sa|sb) if sa|sb else 0
            if sim>best[0]:best=(sim,b['url'])
        if best[0]>=.5:
            findings.append({'article_url':a['url'],'similar_to':best[1], 'character_12gram_jaccard':best[0]})
    return findings


def join(articles, official, data, aliases, excluded):
    by_date = defaultdict(list)
    for r in official:
        by_date[r['date']].append(r)
    offers = defaultdict(list)
    for _,r in data.iterrows():
        offers[(r.sport,str(r.date)[:10],r.home_team,r.away_team,r.league)].append(r)
    candidates = defaultdict(list)
    rejected = []
    for a in articles:
        if not a.get('full_article') or not a.get('body') or hashlib.sha256(a['body'].encode()).hexdigest()!=a.get('body_sha256'):
            rejected.append({'url':a['url'],'reason':'missing_full_article_or_hash_mismatch'})
            continue
        hits = identity_candidates(a,by_date[article_date(a['title'])],aliases)
        if a.get('native_kickoff'):
            t=datetime.fromisoformat(a['native_kickoff'])
            hits=[r for r in hits if abs((datetime.fromisoformat(r['kickoff'])-t).total_seconds())<=300]
        if len(hits)!=1:
            rejected.append({'url':a['url'],'reason':'official_identity','candidates':len(hits)})
            continue
        r=hits[0]
        try:
            p=datetime.fromisoformat(a['displayed_published'])
            if p.tzinfo is None:
                p=p.replace(tzinfo=timezone(timedelta(hours=9)))
            before=p<datetime.fromisoformat(r['kickoff'])
        except (KeyError,TypeError,ValueError):
            before=False
        if before and a.get('native_date_modified'):
            try:
                before=datetime.fromisoformat(a['native_date_modified'])<datetime.fromisoformat(r['kickoff'])
            except (TypeError,ValueError):
                before=False
        if not before:
            rejected.append({'url':a['url'],'reason':'publication_not_known_pregame'})
            continue
        tip=source_tip(a,r,aliases)
        if tip is None:
            rejected.append({'url':a['url'],'reason':'ambiguous_tip'})
            continue
        source=urlsplit(a['url']).netloc.removeprefix('www.')
        candidates[(source,*fixture_key(r))].append((a,r,tip))
    ledger=[]
    for key,group in sorted(candidates.items()):
        if len({x[1]['official_id'] for x in group})!=1:
            rejected.append({'fixture':list(key),'reason':'multiple_official_ids_same_day_pair'})
            continue
        if len({x[2] for x in group})!=1:
            rejected.append({'fixture':list(key),'reason':'conflicting_same_site_votes'})
            continue
        a,r,tip=sorted(group,key=lambda x:x[0]['url'])[0]
        found=offers[(*fixture_key(r),r['league'])]
        if len(found)!=1:
            rejected.append({'url':a['url'],'reason':'missing_or_ambiguous_odds','count':len(found)})
            continue
        offer=found[0]
        if [int(offer.home_score),int(offer.away_score)]!=[r['home_score'],r['away_score']]:
            rejected.append({'url':a['url'],'reason':'score_conflict'})
            continue
        if r['home_score']==r['away_score'] and r['sport']!='sc':
            continue
        winner=0 if r['home_score']>r['away_score'] else (1 if r['home_score']==r['away_score'] or r['sport']!='sc' else 2)
        if int(offer.y)!=winner:
            raise ValueError('outcome encoding mismatch')
        m=np.asarray(offer.market)
        hybrid=.9*m+.1*np.eye(len(m))[tip]
        ledger.append(dict(**r,site=key[0],article_url=a['url'],original_title=a.get('original_title',a['title']),
                           article_author=a.get('author'),article_body_sha256=a['body_sha256'],
                           displayed_published=a['displayed_published'],observed_at=a['observed_at'],
                           article_pick=a.get('pick'),native_pick=a.get('native_pick'),
                           native_kickoff=a.get('native_kickoff'),native_published=a.get('native_date_published'),
                           native_modified=a.get('native_date_modified'),source_duplicates=[x[0]['url'] for x in group],
                           prior_fixture=fixture_key(r) in excluded, odds=list(offer.odds),winner=winner,
                           market=m.tolist(),hybrid=hybrid.tolist(),
                           picks={'tipster':tip,'market90_tipster10':int(np.argmax(np.round(hybrid,4)))}))
    return ledger,rejected


def main():
    ap=argparse.ArgumentParser()
    for name in ('games','aliases','previous','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    for name in ('articles','official'):
        ap.add_argument('--'+name,type=Path,nargs='+',required=True)
    ap.add_argument('--reference-articles',type=Path,nargs='*',default=[])
    args=ap.parse_args()
    articles={a['url']:a for p in args.articles for a in json.loads(p.read_text(encoding='utf8'))}
    official={r['official_id']:r for p in args.official for r in json.loads(p.read_text(encoding='utf8'))}
    previous=json.loads(args.previous.read_text(encoding='utf8'))['ledger']
    aliases=json.loads(args.aliases.read_text(encoding='utf8'))
    data,quality=load_data(args.games)
    keys=['kickoff','sport','league','home_team','away_team']
    scores=load_matches(path=args.games)[keys+['home_score','away_score']]
    data=data.merge(scores,on=keys,validate='one_to_one')
    ledger,rejected=join(list(articles.values()),list(official.values()),data,aliases,{fixture_key(r) for r in previous})
    groups={}
    for site in sorted({r['site'] for r in ledger}):
        site_rows=[r for r in ledger if r['site']==site]
        for year in sorted({r['date'][:4] for r in site_rows}):
            rows=[r for r in site_rows if r['date'].startswith(year)]
            groups[site+'/'+year]=dict(all_matched=comparison(rows),new_fixtures=comparison([r for r in rows if not r['prior_fixture']]),
                                     sports={s:comparison([r for r in rows if r['sport']==s]) for s in sorted({r['sport'] for r in rows})})
    refs=[a for p in args.reference_articles for a in json.loads(p.read_text(encoding='utf8'))]
    overlaps=content_overlap(ledger,articles,refs)
    out=dict(protocol='Fixed standalone vote vs Shin market90+vote10; 10000 KRW single, joint monthly -50000 KRW, daily reservation/next-day settlement proxy',
             strict_pregame_certified=0,full_articles=len(articles),matched=len(ledger),groups=groups,
             content_copy_candidates=overlaps, reference_articles=len(refs),
             rejected_counts=dict(Counter(r['reason'] for r in rejected)),rejected=rejected,ledger=ledger,quality=quality,
             input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.games,args.aliases,args.previous,*args.articles,*args.official,*args.reference_articles]})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'matched':len(ledger),'groups':{k:{'n':v['all_matched']['n'],'cap':{a:d['profit_krw'] for a,d in v['all_matched']['capped'].items()}} for k,v in groups.items()},'rejections':out['rejected_counts']},ensure_ascii=False))


if __name__=='__main__':
    main()
