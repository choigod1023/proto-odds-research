import argparse
import collections
import datetime as dt
import hashlib
import gzip
import json
from pathlib import Path

DEFAULT = Path(r'C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028')
OUT = Path('experiments/matched-basketball-20261104')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def timestamp(value):
    try:
        x = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return x if x.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None

def settle(side, home, away, odds):
    if home == away:
        return 0.0
    return round(odds - 1, 6) if (home > away) == (side == 'home') else -1.0

def classify(x):
    reasons = []
    if x.get('market') != 'moneyline': reasons.append('different_or_unparsed_market')
    if x.get('period') != 'full_game_including_overtime': reasons.append('overtime_scope_unverified')
    if x.get('side') not in ('home', 'away'): reasons.append('side_unmapped')
    if not x.get('fixture_id') or x.get('result_evidence') != 'official_exact_fixture': reasons.append('official_fixture_missing')
    if x.get('home_score') is None or x.get('away_score') is None: reasons.append('official_score_missing')
    odds = x.get('proto_odds')
    if not isinstance(odds, list) or len(odds) != 2 or any(not isinstance(v, (int, float)) or v <= 1 for v in odds): reasons.append('two_way_price_missing')
    if x.get('proto_offer_evidence') != 'archived_exact_fixture': reasons.append('price_fixture_unverified')
    if x.get('within_article_conflicting_directions'): reasons.append('source_conflict')
    if not (x.get('author_id') or x.get('author')): reasons.append('author_missing')
    return reasons

def run(root, out):
    files = {'joined': root/'joined-standard-ot.json', 'candidates': root/'basketball/candidates.jsonl', 'offers': root/'prices/offers.jsonl', 'nba_results': root/'official/nba-results.json', 'kbl_results': root/'basketball/official-kbl-results.jsonl'}
    hashes = {k: sha(v) for k,v in files.items()}
    joined = json.loads(files['joined'].read_text(encoding='utf8'))
    rows = [x for x in joined if x.get('league') in ('NBA','KBL')]
    candidates = [json.loads(x) for x in files['candidates'].read_text(encoding='utf8').splitlines() if x]
    offers = [json.loads(x) for x in files['offers'].read_text(encoding='utf8').splitlines() if x]
    results = json.loads(files['nba_results'].read_text(encoding='utf8'))
    results += [json.loads(x) for x in files['kbl_results'].read_text(encoding='utf8').splitlines() if x]
    result_map = {x['official_id']: x for x in results}
    candidate_audit=[]
    for candidate in candidates:
        checked=[]
        for e in candidate.get('evidence', []):
            path=root.parents[1] / e.get('raw_path','')
            try:
                raw=gzip.decompress(path.read_bytes())
                valid=hashlib.sha256(raw).hexdigest()==e.get('sha256')
            except (OSError, EOFError):
                valid=False
            checked.append({'path':e.get('raw_path'),'sha256':e.get('sha256'),'valid':valid})
        candidate_audit.append({'candidate_id':candidate.get('candidate_id'),'league':candidate.get('league'),'market':candidate.get('market'),'source_url':candidate.get('source_url'),'evidence':checked})
    audits=[]; groups=collections.defaultdict(list)
    for i,x in enumerate(rows):
        reasons=classify(x); rid=x.get('fixture_id'); result=result_map.get(rid)
        if not result: reasons.append('result_record_not_found')
        elif (result.get('home_score'),result.get('away_score')) != (x.get('home_score'),x.get('away_score')): reasons.append('result_score_mismatch')
        kickoff=x.get('kickoff') or (result or {}).get('kickoff'); date=(kickoff or x.get('match_date') or '')[:10]
        possible=[o for o in offers if o.get('league')==x.get('league') and o.get('date')==date and o.get('market')=='full_game_moneyline' and o.get('odds')==x.get('proto_odds') and (o.get('home_score'),o.get('away_score'))==(x.get('home_score'),x.get('away_score'))]
        if len(possible)!=1: reasons.append('price_raw_ambiguous_or_missing')
        claim=timestamp(x.get('published_at_claim') or x.get('source_reported_published_at')); start=timestamp(kickoff)
        claim_before=bool(claim and start and claim < start)
        observed=timestamp(x.get('observed_at'))
        strict=bool(x.get('cutoff_proven') and observed and start and observed < start)
        r={'row_index':i,'league':x.get('league'),'date':date,'fixture_id':rid,'source':x.get('source'),'author':x.get('author_id') or x.get('author'),'source_url':x.get('source_url') or x.get('url'),'source_sha256':x.get('source_sha256') or x.get('document_sha256') or [e.get('sha256') for e in x.get('evidence',[])], 'source_observed_at':x.get('observed_at'),'published_claim':x.get('published_at_claim') or x.get('source_reported_published_at'),'modified_claim':x.get('source_reported_modified_at'),'kickoff':kickoff,'strict_source_receipt':strict,'claim_before_kickoff':claim_before,'side':x.get('side'),'market':x.get('market'),'home':x.get('home'),'away':x.get('away'),'price':x.get('proto_odds'),'score':[x.get('home_score'),x.get('away_score')],'result_url':x.get('result_source_url'),'price_raw':possible[0] if len(possible)==1 else None,'reasons':reasons}
        audits.append(r)
        if not reasons: groups[(x['league'],rid)].append(r)
    comparisons=[]
    for (league,rid),rs in sorted(groups.items()):
        # Only a single price vector is admitted: multiple distinct snapshots have no ordering evidence.
        vectors={tuple(r['price']) for r in rs}
        if len(vectors)!=1:
            for r in rs:r['reasons'].append('conflicting_price_vectors')
            continue
        byauthor=collections.defaultdict(list)
        for r in rs:byauthor[(r['source'],str(r['author']).casefold())].append(r)
        votes=[]
        for author, ar in byauthor.items():
            if len({r['side'] for r in ar})==1:votes.append(ar[0])
            else:
                for r in ar:r['reasons'].append('author_conflicting_sides')
        if not votes: continue
        # Separate claimed-pre-game votes from source rows lacking a timestamp.
        for category, subset in [('historical_posted_claim', [r for r in votes if r['claim_before_kickoff']]),('retrospective_undated', [r for r in votes if not r['claim_before_kickoff']])]:
            if not subset:continue
            home=sum(r['side']=='home' for r in subset); away=len(subset)-home
            if home==away: continue
            r=subset[0]; odds=r['price']; market='home' if odds[0]<odds[1] else 'away' if odds[1]<odds[0] else None
            if market is None:continue
            vote='home' if home>away else 'away'; h,a=r['score']
            comparisons.append({'category':category,'league':league,'date':r['date'],'season':f"{int(r['date'][:4]) if int(r['date'][5:7])>=7 else int(r['date'][:4])-1}-{(int(r['date'][:4]) if int(r['date'][5:7])>=7 else int(r['date'][:4])-1)+1}",'fixture_id':rid,'home':r['home'],'away':r['away'],'kickoff':r['kickoff'],'price':odds,'price_raw':r['price_raw'],'score':[h,a],'result_url':r['result_url'],'votes':[{'author':v['author'],'source':v['source'],'side':v['side'],'source_sha256':v['source_sha256'],'source_url':v['source_url'],'observed_at':v['source_observed_at'],'published_claim':v['published_claim'],'modified_claim':v['modified_claim']} for v in subset], 'vote_selection':vote,'market_selection':market,'vote_unit_profit':settle(vote,h,a,odds[0 if vote=='home' else 1]),'market_unit_profit':settle(market,h,a,odds[0 if market=='home' else 1]),'player_model':'not_implemented','hybrid_9_1':'not_implemented'})
    out.mkdir(parents=True,exist_ok=True)
    for name,data in [('audit.jsonl',audits),('comparisons.jsonl',comparisons),('candidate-raw-audit.jsonl',candidate_audit)]:
        (out/name).write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in data),encoding='utf8')
    stats=collections.defaultdict(lambda: {'fixtures':0,'vote_profit':0.,'market_profit':0.})
    for r in comparisons:
        for period in (r['date'][:7],r['season'],'all'):
            s=stats[(r['category'],r['league'],period)];s['fixtures']+=1;s['vote_profit']+=r['vote_unit_profit'];s['market_profit']+=r['market_unit_profit']
    summary={'input_sha256':hashes,'candidate_count':len(candidates),'candidate_raw_at_least_one_verified':sum(any(e['valid'] for e in c['evidence']) for c in candidate_audit),'joined_basketball_rows':len(rows),'audit_reasons':dict(collections.Counter(reason for r in audits for reason in r['reasons'])),'strict_source_receipt_rows':sum(r['strict_source_receipt'] for r in audits),'comparison_rows':len(comparisons),'unique_fixtures':len({(r['league'],r['fixture_id']) for r in comparisons}),'strata':[{'category':a,'league':b,'period':c,**v} for (a,b,c),v in sorted(stats.items())]}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=DEFAULT);p.add_argument('--output',type=Path,default=OUT);a=p.parse_args();print(json.dumps(run(a.input,a.output),ensure_ascii=False,indent=2))
