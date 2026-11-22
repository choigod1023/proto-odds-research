import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import re
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT = Path(r'C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028')
OUT = Path('experiments/matched-basketball-20261104')
SEASON = Path('experiments/pregame-basketball-20261104/season-records.jsonl')
# Explicit aliases; source labels, official names and price labels independently map to the same code.
ALIASES = {
 'KBL': {'70':['Anyang JungKwanJang','안양 정관장 레드부스터스','안양정관'], '66':['Goyang Sono','고양 소노 스카이거너스','고양소노'], '60':['KCC Egis','부산 KCC 이지스','부산KCC'], '64':['KoGas','대구 한국가스공사 페가수스','한국가스'], '50':['LG Sakers','창원 LG 세이커스','창원LG'], '10':['Mobis Phoebus','울산 현대모비스 피버스','울산모비'], '55':['Seoul Knights','서울 SK 나이츠','서울SK'], '06':['Suwon KT','수원 KT 소닉붐','KT소닉붐'], '16':['Wonju DB','원주 DB 프로미','원주DB']},
 'NBA': {'ATL':['Atlanta Hawks','애틀호크'], 'BOS':['Boston Celtics','보스셀틱'], 'BKN':['Brooklyn Nets','브루네츠'], 'CHA':['Charlotte Hornets','샬럿호네'], 'CHI':['Chicago Bulls','시카불스'], 'DET':['Detroit Pistons','디트피스'], 'HOU':['Houston Rockets','휴스로케'], 'LAL':['Los Angeles Lakers','LA레이커'], 'MIL':['Milwaukee Bucks','밀워벅스'], 'MIN':['Minnesota Timberwolves','미네울브'], 'NO':['New Orleans Pelicans','뉴올펠리'], 'OKC':['Oklahoma City Thunder','오클썬더'], 'ORL':['Orlando Magic','올랜매직'], 'PHI':['Philadelphia 76ers','필라76s'], 'PHX':['Phoenix Suns','피닉선즈'], 'SA':['San Antonio Spurs','샌안스퍼'], 'TOR':['Toronto Raptors','토론랩터'], 'UTA':['Utah Jazz','유타재즈']}}

def code(league, name):
    matches=[k for k, labels in ALIASES.get(league,{}).items() if name in labels]
    return matches[0] if len(matches)==1 else None

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def timestamp(value):
    try:
        x=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        return x if x.tzinfo else None
    except (TypeError, ValueError, AttributeError): return None

def settle(side,home,away,odds):
    if home==away:return 0.0
    return round(odds-1,6) if (home>away)==(side=='home') else -1.0

def choose(prob):
    if abs(prob-.5)<1e-12:return None
    return 'home' if prob>.5 else 'away'

def probabilities(odds, sides):
    if len(odds)!=2 or any(not isinstance(o,(int,float)) or o<=1 for o in odds) or not sides: raise ValueError('invalid inputs')
    market=(1/odds[0])/(1/odds[0]+1/odds[1]); vote=sides.count('home')/len(sides)
    return market,vote,.9*market+.1*vote

def recover_nba(out):
    """One request per NBA ET date, 15s timeout, stop on 403/429; preserve bytes and completion time."""
    audit=out/'audit.jsonl'
    rows=(json.loads(x) for x in audit.read_text(encoding='utf8').splitlines())
    dates=sorted({r['fixture']['raw_meta']['date'] for r in rows if r['league']=='NBA' and r.get('fixture') and r['fixture'].get('raw_meta') and not (set(r['reasons'])-{'nba_official_raw_missing_or_mismatch'})})
    cache=out/'nba-official-raw';cache.mkdir(parents=True,exist_ok=True)
    for day in dates[:12]:
        body_path=cache/(day+'.html.gz'); meta_path=cache/(day+'.json')
        if body_path.exists() and meta_path.exists():
            meta=json.loads(meta_path.read_text(encoding='utf8'));body=gzip.decompress(body_path.read_bytes())
            if hashlib.sha256(body).hexdigest()!=meta['sha256']:raise ValueError('cached NBA SHA mismatch')
            if meta['status'] in (403,429):break
            continue
        url='https://www.nba.com/games?date='+day
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; research audit)'})
        try:
            with urllib.request.urlopen(request,timeout=15) as response:
                body=response.read(); status=response.status; final_url=response.geturl()
        except urllib.error.HTTPError as e:
            body=e.read();status=e.code;final_url=e.geturl()
        except (OSError,TimeoutError) as e:
            meta_path.write_text(json.dumps({'url':url,'error':str(e),'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()},indent=2),encoding='utf8');break
        completed=dt.datetime.now(dt.timezone.utc).isoformat()
        body_path.write_bytes(gzip.compress(body,mtime=0))
        meta={'url':url,'final_url':final_url,'status':status,'completed_at':completed,'timestamp_basis':'http_response_body_completed','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
        meta_path.write_text(json.dumps(meta,indent=2)+'\n',encoding='utf8')
        print(day,status,len(body))
        if status in (403,429) or status!=200:break


def verify_nba_raw(out, fixture):
    day=fixture['raw_meta']['date']; directory=out/'nba-official-raw'
    try:
        meta=json.loads((directory/(day+'.json')).read_text(encoding='utf8'))
        body=gzip.decompress((directory/(day+'.html.gz')).read_bytes())
        if meta['status']!=200 or hashlib.sha256(body).hexdigest()!=meta['sha256']:return None
        match=re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',body.decode('utf8'))
        props=json.loads(match.group(1))['props']['pageProps'];feed=props['gameCardFeed']
        if props['selectedDate']!=day or feed.get('nextPageToken') is not None:return None
        games=[c['cardData'] for module in feed['modules'] for c in module.get('cards',[]) if c['cardData'].get('gameId')==fixture['id'].removeprefix('nba:')]
        if len(games)!=1:return None
        game=games[0];home=game['homeTeam'];away=game['awayTeam'];aliases={'NOP':'NO','SAS':'SA','GSW':'GS','NYK':'NY'}
        hc=aliases.get(home['teamTricode'],home['teamTricode']);ac=aliases.get(away['teamTricode'],away['teamTricode'])
        if game['gameStatus']!=3 or (hc,ac)!=(fixture['home_code'],fixture['away_code']) or [home['score'],away['score']]!=fixture['score'] or timestamp(game['gameTimeUtc'])!=timestamp(fixture['kickoff']):return None
        return {'url':meta['url'],'completed_at':meta['completed_at'],'sha256':meta['sha256'],'period':game.get('period'),'home_periods':home.get('periods'),'away_periods':away.get('periods')}
    except (OSError,ValueError,KeyError,AttributeError,TypeError):return None


def run(root,out):
    files={'candidates':root/'basketball/candidates.jsonl','offers':root/'prices/offers.jsonl','nba_results':root/'official/nba-results.json','nba_requests':root/'official/nba-date-requests.json','kbl_results':root/'basketball/official-kbl-results.jsonl','kbl_season':SEASON}
    hashes={k:sha(v) for k,v in files.items()}
    readlines=lambda p:[json.loads(line) for line in p.read_text(encoding='utf8').splitlines() if line]
    candidates=readlines(files['candidates']);offers=readlines(files['offers']);nba=json.loads(files['nba_results'].read_text(encoding='utf8')); kbl=readlines(files['kbl_results']); season=readlines(files['kbl_season']); requests=json.loads(files['nba_requests'].read_text(encoding='utf8'))
    seasons={x['payload']['source_fixture_id']:x for x in season if x.get('league')=='KBL' and x.get('kind')=='fixture'}
    req={x['date']:x for x in requests if x.get('status')==200}
    official=[]
    for r in nba:
        h,a=code('NBA',r['home']),code('NBA',r['away'])
        if h and a:official.append({'league':'NBA','id':r['official_id'],'date':r['match_date'],'home_code':h,'away_code':a,'home':r['home'],'away':r['away'],'kickoff':r['kickoff'],'score':[r['home_score'],r['away_score']],'url':r['source_url'],'raw_meta':req.get(r.get('official_date'))})
    for r in kbl:
        f=seasons.get(r['official_id']); names=r['teams'];h,a=code('KBL',names['home']),code('KBL',names['away'])
        try:
            raw=gzip.decompress((root.parents[1]/r['evidence']['raw_path']).read_bytes())
            raw_valid=hashlib.sha256(raw).hexdigest()==r['evidence']['sha256']
        except (OSError,KeyError,EOFError):raw_valid=False
        if raw_valid and f and h==r['home_id'] and a==r['away_id'] and f['payload']['home_team_id']==h and f['payload']['away_team_id']==a:
            official.append({'league':'KBL','id':r['official_id'],'date':r['match_date'],'home_code':h,'away_code':a,'home':names['home'],'away':names['away'],'kickoff':f['kickoff'],'score':[r['home_score'],r['away_score']],'url':r['source_url'],'raw_meta':r['evidence']})
    audit=[];groups=collections.defaultdict(list)
    for c in candidates:
        league=c['league']; reasons=[]; valid=[]
        for e in c.get('evidence',[]):
            try:valid.append(hashlib.sha256(gzip.decompress((root.parents[1]/e['raw_path']).read_bytes())).hexdigest()==e['sha256'])
            except (OSError, EOFError):valid.append(False)
        if not any(valid):reasons.append('source_raw_unverified')
        if c.get('market')!='moneyline' or c.get('market_raw')!='H/A, OT' or c.get('market_scope')!='full_game_including_overtime':reasons.append('not_explicit_moneyline_ot')
        hc=code(league,c.get('teams',{}).get('home'));ac=code(league,c.get('teams',{}).get('away'))
        if not hc or not ac or hc==ac:reasons.append('unmapped_source_teams')
        matches=[r for r in official if r['league']==league and r['date']==c.get('match_date') and (r['home_code'],r['away_code'])==(hc,ac)]
        if len(matches)!=1:reasons.append('official_fixture_missing_or_ambiguous')
        r=matches[0] if len(matches)==1 else None
        if r and not r['raw_meta']:reasons.append('official_raw_metadata_missing')
        if r and league=='NBA' and r.get('raw_meta'):
            verified=verify_nba_raw(out,r)
            if not verified:reasons.append('nba_official_raw_missing_or_mismatch')
            else:r=dict(r,verified_raw=verified)
        prices=[]
        if r:
            prices=[o for o in offers if o.get('league')==league and o.get('date')==r['date'] and o.get('market')=='full_game_moneyline' and code(league,o.get('home'))==hc and code(league,o.get('away'))==ac and timestamp(o.get('kickoff'))==timestamp(r['kickoff'])]
        if len(prices)!=1:reasons.append('price_missing_or_ambiguous')
        p=prices[0] if len(prices)==1 else None
        if p and (not isinstance(p.get('odds'),list) or len(p['odds'])!=2 or p.get('odds_order')!=['home','away']):reasons.append('invalid_price')
        if p and [p.get('home_score'),p.get('away_score')]!=r['score']:reasons.append('score_disagreement')
        if c.get('side') not in ('home','away') or not (c.get('author_id') or c.get('author')):reasons.append('invalid_vote')
        obs=timestamp(c.get('observed_at')); start=timestamp(r['kickoff']) if r else None;claim=timestamp(c.get('published_at_claim') or c.get('source_reported_published_at'))
        row={'candidate_id':c.get('candidate_id'),'league':league,'match_date':c.get('match_date'),'market_raw':c.get('market_raw'),'source':c.get('source'),'source_url':c.get('source_url'),'source_observed_at':c.get('observed_at'),'published_claim':c.get('published_at_claim') or c.get('source_reported_published_at'),'strict_source_receipt':bool(c.get('cutoff_proven') and obs and start and obs<start),'claim_before_kickoff':bool(claim and start and claim<start),'author':c.get('author_id') or c.get('author'),'side':c.get('side'),'source_teams':c.get('teams'),'source_evidence':c.get('evidence'),'source_evidence_valid':valid,'fixture':r,'price':p,'reasons':reasons}
        audit.append(row)
        if not reasons:groups[(league,r['id'])].append(row)
    comparisons=[]
    for key,rows in sorted(groups.items()):
        # One physical fixture, claimed publication votes take precedence; never mix evidence cohorts.
        claimed=[r for r in rows if r['claim_before_kickoff']]; subset=claimed or rows
        category='historical_posted_claim' if claimed else 'retrospective_no_pregame_claim'
        authors=collections.defaultdict(list)
        for r in subset:authors[(r['source'],str(r['author']).casefold())].append(r)
        votes=[]
        for ar in authors.values():
            if len({r['side'] for r in ar})==1:votes.append(sorted(ar,key=lambda x:x['candidate_id'])[0])
            else:
                for r in ar:r['reasons'].append('author_conflicting_sides')
        if not votes:continue
        r=votes[0]; f=r['fixture']; p=r['price'];odds=p['odds']; sides=[v['side'] for v in votes]; market,vote,mix=probabilities(odds,sides)
        selections={'market':choose(market),'vote':choose(vote),'multiplicative_9_1':choose(mix)}
        if any(s is None for s in selections.values()):
            for r in rows:r['reasons'].append('selection_tie')
            continue
        profit={k:settle(v,*f['score'],odds[0 if v=='home' else 1]) for k,v in selections.items()}
        h,a=f['score'];outcome='refund' if h==a else 'home' if h>a else 'away'
        comparisons.append({'category':category,'league':key[0],'fixture_id':key[1],'date':f['date'],'season':f"{int(f['date'][:4]) if int(f['date'][5:7])>=7 else int(f['date'][:4])-1}-{(int(f['date'][:4]) if int(f['date'][5:7])>=7 else int(f['date'][:4])-1)+1}",'fixture':f,'price':p,'votes':[{'author':v['author'],'source':v['source'],'side':v['side'],'candidate_id':v['candidate_id'],'source_url':v['source_url'],'source_evidence':v['source_evidence'],'observed_at':v['source_observed_at'],'published_claim':v['published_claim']} for v in votes],'market_home_probability':market,'vote_home_share':vote,'multiplicative_9_1_home_score':mix,'selections':selections,'outcome':outcome,'unit_profit':profit,'player_model':'not_implemented'})
    assert len(comparisons)==len({(r['league'],r['fixture_id']) for r in comparisons})
    out.mkdir(parents=True,exist_ok=True)
    for name,data in [('audit.jsonl',audit),('comparisons.jsonl',comparisons)]: (out/name).write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in data),encoding='utf8')
    stats=collections.defaultdict(lambda:{'fixtures':0,'methods':{k:{'hits':0,'losses':0,'refunds':0,'stakes':0,'profit':0} for k in ('market','vote','multiplicative_9_1')}})
    for r in comparisons:
        for period in (r['date'][:7],r['date'][:4],r['season'],'all'):
            s=stats[(r['category'],r['league'],period)];s['fixtures']+=1
            for k,v in s['methods'].items():
                v['stakes']+=1;v['profit']+=r['unit_profit'][k];v['refunds']+=r['outcome']=='refund';v['hits']+=r['outcome']==r['selections'][k];v['losses']+=r['outcome'] not in (r['selections'][k],'refund')
    strata=[]
    for (category,league,period),v in sorted(stats.items()):
        for m in v['methods'].values():m['profit']=round(m['profit'],6);m['roi']=round(m['profit']/m['stakes'],6)
        strata.append({'category':category,'league':league,'period':period,**v})
    summary={'input_sha256':hashes,'candidate_count':len(candidates),'candidate_raw_at_least_one_verified':sum(any(r['source_evidence_valid']) for r in audit),'audit_reasons':dict(collections.Counter(x for r in audit for x in r['reasons'])),'strict_source_receipt_rows':sum(r['strict_source_receipt'] for r in audit),'comparison_rows':len(comparisons),'unique_fixtures':len(comparisons),'strata':strata}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=DEFAULT);p.add_argument('--output',type=Path,default=OUT);p.add_argument('--recover-nba',action='store_true');a=p.parse_args();
    if a.recover_nba:recover_nba(a.output)
    else:print(json.dumps(run(a.input,a.output),ensure_ascii=False,indent=2))
