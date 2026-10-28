"""Public retrospective archive collection, isolated from production.

Commands: collect (cached/resumable), build (offline), check (offline).
Sampling: first 25 posts in ascending claimed publication order per calendar
month and Tony category; first 18 dated news URLs per month/site/league.
No outcome, author or quoted price participates in sample selection.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlparse
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/tipster-year-20261028/overseas'
START, END = '2025-10-28', '2026-10-27'
ALLOWED = {'www.tonyspicks.com','www.covers.com','www.pickswise.com',
           'statsapi.mlb.com','cdn.nba.com','npb.jp','www.nba.com'}
LAST = {}

def now(): return datetime.now(timezone.utc).isoformat()
def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def read(name, default):
    p=OUT/name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
def jsonl(name, rows):
    (OUT/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def fetch(url):
    host=urlparse(url).hostname
    if host not in ALLOWED: raise ValueError(host)
    key=hashlib.sha256(url.encode()).hexdigest()
    cache=OUT/'cache'/f'{key}.json.gz'
    if cache.exists():
        with gzip.open(cache,'rt',encoding='utf-8') as f:entry=json.load(f)
        if host=='npb.jp' and '\u00e3' in entry.get('body',''):
            try:entry['body']=entry['body'].encode('latin1').decode('utf-8')
            except (UnicodeEncodeError,UnicodeDecodeError):pass
        return entry
    time.sleep(max(0,0.55-(time.monotonic()-LAST.get(host,0))))
    entry={'url':url,'requested_at':now()}
    try:
        r=requests.get(url,timeout=(6,18),allow_redirects=False,
                       headers={'User-Agent':'PublicHistoricalResearch/1.0'})
        entry.update(status=r.status_code,observed_at=now(),sha256=hashlib.sha256(r.content).hexdigest(),
                     bytes=len(r.content),total=r.headers.get('X-WP-Total'),pages=r.headers.get('X-WP-TotalPages'))
        entry['body']=r.content.decode(r.encoding if 'charset=' in r.headers.get('Content-Type','').lower() else 'utf-8',errors='replace') if r.status_code==200 else ''
        if r.status_code!=200:entry['error']=f'http_{r.status_code}'
    except requests.RequestException as e:entry.update(error=str(e)[:180],observed_at=now(),body='')
    LAST[host]=time.monotonic()
    cache.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(cache,'wt',encoding='utf-8') as f:json.dump(entry,f)
    with (OUT/'requests.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({k:v for k,v in entry.items() if k!='body'})+'\n')
    return entry

def clean(t):return ' '.join(html.unescape(t).replace('\u2212','-').replace('\u2013','-').split())
def author_name(x):
    if isinstance(x,list):return '; '.join(filter(None,[author_name(v) for v in x])) or None
    if isinstance(x,dict):return x.get('name') or author_name(x.get('mainEntity'))
    return x if isinstance(x,str) else None
def metadata(s):
    out={}
    def scan(x):
        if isinstance(x,list):
            for v in x:scan(v)
        if isinstance(x,dict):
            if x.get('@type') in ('NewsArticle','Article','BlogPosting'):
                for k in ('datePublished','dateModified','author','headline'):out.setdefault(k,x.get(k))
            for v in x.values():
                if isinstance(v,(list,dict)):scan(v)
    for node in s.select('script[type="application/ld+json"]'):
        try:scan(json.loads(node.get_text()))
        except (ValueError,TypeError):pass
    return out

# Names are league-specific: never resolve Giants across NPB and MLB.
MLB=['Arizona Diamondbacks','Atlanta Braves','Baltimore Orioles','Boston Red Sox','Chicago Cubs',
     'Chicago White Sox','Cincinnati Reds','Cleveland Guardians','Colorado Rockies','Detroit Tigers',
     'Houston Astros','Kansas City Royals','Los Angeles Angels','Los Angeles Dodgers','Miami Marlins',
     'Milwaukee Brewers','Minnesota Twins','New York Mets','New York Yankees','Athletics',
     'Philadelphia Phillies','Pittsburgh Pirates','San Diego Padres','San Francisco Giants',
     'Seattle Mariners','St. Louis Cardinals','Tampa Bay Rays','Texas Rangers','Toronto Blue Jays','Washington Nationals']
NBA=['Atlanta Hawks','Boston Celtics','Brooklyn Nets','Charlotte Hornets','Chicago Bulls','Cleveland Cavaliers',
     'Dallas Mavericks','Denver Nuggets','Detroit Pistons','Golden State Warriors','Houston Rockets',
     'Indiana Pacers','Los Angeles Clippers','Los Angeles Lakers','Memphis Grizzlies','Miami Heat',
     'Milwaukee Bucks','Minnesota Timberwolves','New Orleans Pelicans','New York Knicks',
     'Oklahoma City Thunder','Orlando Magic','Philadelphia 76ers','Phoenix Suns','Portland Trail Blazers',
     'Sacramento Kings','San Antonio Spurs','Toronto Raptors','Utah Jazz','Washington Wizards']
NPB=['Yomiuri Giants','Tokyo Yakult Swallows','Yokohama DeNA BayStars','Chunichi Dragons',
     'Hanshin Tigers','Hiroshima Toyo Carp','Hokkaido Nippon-Ham Fighters','Tohoku Rakuten Golden Eagles',
     'Fukuoka SoftBank Hawks','Orix Buffaloes','Chiba Lotte Marines','Saitama Seibu Lions']
NAMES={'MLB':MLB,'NBA':NBA,'NPB':NPB}
ALIASES=defaultdict(dict)
for league,names in NAMES.items():
    for n in names:
        ALIASES[league][n.lower()]=n
        if ' ' in n:ALIASES[league][n.split(' ',1)[1].lower()]=n
        ALIASES[league][n.split()[-1].lower()]=n
for short,full in {'NY Yankees':'New York Yankees','NY Mets':'New York Mets','LA Angels':'Los Angeles Angels',
                   'LA Dodgers':'Los Angeles Dodgers','St Louis':'St. Louis Cardinals','St Louis Cardinals':'St. Louis Cardinals',
                   'Oakland Athletics':'Athletics','Sacramento Athletics':'Athletics'}.items():ALIASES['MLB'][short.lower()]=full
for short,full in {'Nippon-Ham Fighters':NPB[6],'Rakuten Eagles':NPB[7],'Rakuten Golden Eagles':NPB[7],
                   'Hiroshima Carp':NPB[5],'Yokohama BayStars':NPB[2],'SoftBank Hawks':NPB[8]}.items():ALIASES['NPB'][short.lower()]=full
for code,full in zip(['Yomiuri','Yakult','Yokohama','Chunichi','Hanshin','Hiroshima','Nippon-Ham','Rakuten','SoftBank','Orix','Lotte','Seibu'],NPB):ALIASES['NPB'][code.lower()]=full
def teams_in(text,league):
    text=clean(text).lower().replace('-',' ')
    hits=[]
    for a,n in ALIASES[league].items():
        a=a.replace('-',' ')
        m=re.search(r'(?<![a-z])'+re.escape(a)+r'(?![a-z])',text)
        if m:hits.append((m.start(),m.end(),n))
    # A short alias inside a longer name must not invent another club
    # (e.g. Sox inside Boston Red Sox must not become Chicago White Sox).
    nonoverlap=[]
    for hit in sorted(hits,key=lambda h:-(h[1]-h[0])):
        if not any(hit[0]<old[1] and old[0]<hit[1] for old in nonoverlap):nonoverlap.append(hit)
    ans=[]
    for _,_,n in sorted(nonoverlap):
        if n not in ans:ans.append(n)
    return ans
def resolve_selection(text,teams,league):
    found=[n for n in teams_in(text,league) if n in teams]
    if len(found)==1:return found[0]
    # City-only names are accepted only if unique within this matchup.
    cand=[]
    text=text.lower().replace('.','')
    cities=['new york','los angeles','golden state','oklahoma city','new orleans','san antonio','san francisco','san diego','kansas city','tampa bay','st louis','portland','toronto','philadelphia','sacramento','milwaukee','minnesota','washington','chicago','boston','cleveland','atlanta','detroit','houston','texas','miami','pittsburgh','arizona','colorado','cincinnati','baltimore','seattle','memphis','orlando','charlotte','denver','dallas','indiana','phoenix','utah','brooklyn']
    for n in teams:
        full=n.lower().replace('.','')
        city=next((c for c in cities if full.startswith(c+' ')),None)
        aliases=[city] if city else []
        if city=='los angeles':aliases+=['la']
        if city=='new york':aliases+=['ny']
        if city=='oklahoma city':aliases+=['okc']
        if city=='golden state':aliases+=['golden st']
        if city=='san francisco':aliases+=['sf']
        if city=='kansas city':aliases+=['kc']
        if any(re.search(r'\b'+re.escape(a)+r'\b',text) for a in aliases):cand.append(n)
    return cand[0] if len(cand)==1 else None
def decimal(price):
    if abs(price)<100:raise ValueError('invalid American price')
    return 1+(price/100 if price>0 else 100/abs(price))
def match_date(title,fallback):
    m=re.search(r'(?<!\d)(\d{1,2})/(\d{1,2})/(20\d{2}|\d{2})(?!\d)',title)
    if m:
        try:return date(int(m[3])+(2000 if len(m[3])==2 else 0),int(m[1]),int(m[2])).isoformat()
        except ValueError:pass
    m=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:,?\s+(20\d{2}))?',title,re.I)
    if m:
        month=['january','february','march','april','may','june','july','august','september','october','november','december'].index(m[1].lower())+1
        try:return date(int(m[3] or fallback[:4]),month,int(m[2])).isoformat()
        except (ValueError,TypeError):pass
    return fallback[:10] if fallback else None
def document(url,body,entry,wp=None):
    s=BeautifulSoup(body,'html.parser')
    meta=metadata(s)
    title=clean(BeautifulSoup(wp['title']['rendered'],'html.parser').get_text()) if wp else clean(s.h1.get_text(' ',strip=True)) if s.h1 else ''
    if wp:
        meta.update(datePublished=wp.get('date_gmt')+'Z',dateModified=wp.get('modified_gmt')+'Z')
    for n in s.select('script,style,iframe,nav,footer'):n.decompose()
    nodes=[];section=''
    for n in s.select('h1,h2,h3,h4,h5,h6,p,li,tr'):
        t=clean(n.get_text(' ',strip=True))
        if not t:continue
        # Save short recommendation facts and fixture headings, not article bodies.
        if n.name.startswith('h'):
            section=t
            nodes.append({'tag':n.name,'text':t[:240]})
        else:
            for m in re.finditer(r'\bPlay\s+[^.!?]{3,140}(?:\.[05])?[^.!?]{0,30}',t,re.I):
                nodes.append({'tag':'play','text':m.group(0)[:190]})
            pricepat=r'(?<!\d)(?:[+-]\d{3,4}|(?:minus|plus)[ -]+\d{3,4})(?!\d)'
            recsection=bool(re.search(r'official pick|final recommendation|final pick|official.*best bet|prediction$|the pick|best bet$',section,re.I))
            if re.search(r'(?:pick|money.?line|best bet|take|back|over|under)',t,re.I) or recsection:
                for m in re.finditer(pricepat,t,re.I):
                    nodes.append({'tag':'odds_context','text':t[max(0,m.start()-110):m.end()+65]})
                    if recsection:
                        nodes.append({'tag':'recommendation','text':t[max(0,m.start()-100):m.end()+55], 'section':section})
    return {'url':url,'source': 'TonysPicks' if 'tonyspicks' in url else 'Pickswise' if 'pickswise' in url else 'Covers',
            'title':title,'metadata':meta,'wp_author_id':wp.get('author') if wp else None,
            'observed_at':entry['observed_at'],'sha256':entry['sha256'],'provenance_request_url':entry['url'],'nodes':nodes}

def parse(doc,authors):
    url=doc['url'];title=doc['title'];low=(url+' '+title).lower()
    league='NPB' if re.search(r'npb|nippon|softbank|rakuten|yomiuri|seibu|lotte-marines|orix|hanshin|yakult|chunichi|baystars|hiroshima',low) else 'MLB' if 'mlb' in low else 'NBA' if 'nba' in low else None
    if not league:return []
    meta=doc['metadata'];published=meta.get('datePublished');dt=match_date(title,published)
    if not dt or not START<=dt<=END:return []
    author=author_name(meta.get('author'))
    if doc.get('wp_author_id'):author=authors.get(str(doc['wp_author_id']),author)
    titleteams=teams_in(title,league); context=titleteams[:2] if len(titleteams)==2 else []
    rows=[]
    for node in doc['nodes']:
        text=node['text'];tag=node['tag']
        if tag.startswith('h'):
            found=teams_in(text,league)
            if re.search(r'vs\.?| at | over ',text,re.I):context=found if len(found)==2 else []
        isplay=tag=='play' and bool(re.match(r'Play\s+(?:[A-Z]|over\b|under\b)',text))
        ispick=tag.startswith('h') and re.search(r'best bet:|prediction:|pick:',text,re.I) and not re.search(r'score prediction',text,re.I)
        # For individual Tony articles accept only explicit recommendation + price
        # in a short odds context, never a price from the odds overview table.
        explicit=tag in ('odds_context','recommendation') and len(titleteams)==2 and (tag=='recommendation' or bool(re.search(r'\b(?:take\b|back\b|pick is\b|pick:|play:|recommend\b|bet is\b)',text,re.I)))
        if not (isplay or ispick or explicit):continue
        teams=context[:]
        if len(teams)!=2:continue
        normalized_price_text=re.sub(r'\b(minus|plus)[ -]+(\d{3,4})',lambda m:('-' if m[1].lower()=='minus' else '+')+m[2],text,flags=re.I)
        normalized_price_text=re.split(r'Recommended Odds Range:|Playable to ',normalized_price_text,flags=re.I)[0]
        price_matches=list(re.finditer(r'(?<![\d.])([+-]\d{3,4})(?![\d.])',normalized_price_text))
        price=int(price_matches[0][1]) if len(price_matches)==1 else None
        market=None;selection=None;line=None
        t=normalized_price_text
        labels=list(re.finditer(r'(?:best bet|expert pick|official play|final pick|the pick|play|prediction|pick)\s*:\s*',t,re.I))
        if labels:t=t[labels[-1].end():]
        m=re.search(r'\b(over|under)\s+(\d+(?:\.\d+)?)',t,re.I)
        if m:
            market='total';selection=m[1].lower();line=float(m[2])
            if re.search(r'strikeout|hits|rbi|points|rebounds|assists|home run|bases',t,re.I):market='player_prop'
        else:
            selection_text=re.split(r'\s+(?:over|vs\.?)\s+',t,flags=re.I)[0]
            selection=resolve_selection(selection_text,teams,league)
            m=re.search(r'(?<!\d)([+-]\d{1,2}(?:\.\d+)?)(?![\d.])',t)
            if m:market='spread';line=float(m[1])
            elif re.search(r'\bat pick\b|\bpk\b',t,re.I) and selection:market='spread';line=0.0
            elif selection:market='moneyline' if league!='NBA' or re.search(r'\bML\b|money.?line',t,re.I) or price is not None else None
        reasons=[]
        if market=='player_prop':reasons.append('player_stat_required')
        if market is None or selection is None:reasons.append('selection_or_market_unresolved')
        if market=='total':
            mentioned=[n for n in NAMES[league] if resolve_selection(t,[n],league)==n]
            if mentioned and not set(mentioned).intersection(teams):reasons.append('fixture_context_mismatch')
        if price is None:reasons.append('original_price_missing_or_ambiguous')
        approx=bool(re.search(r'around|about|roughly|approximately',text,re.I))
        if approx:reasons.append('approximate_price')
        partial=bool(re.search(r'first (?:five|5)|\bF5\b|first half|1st half|team total',text+' '+node.get('section',''),re.I))
        if partial:reasons.append('partial_game_or_team_total_not_full_game')
        # Headers naming individual experts override article editor attribution.
        am=re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})'s expert pick",text)
        row={'source':doc['source'],'author':am[1] if am else author,'author_identity_status':'source_claimed' if am or author else 'unknown',
             'url':url,'league':league,'match_date':dt,'match_date_timezone':'Asia/Tokyo' if league=='NPB' else 'America/New_York',
             'teams':teams,'market':market,'selection':selection,'line':line,'original_american_odds':price,
             'decimal_odds':decimal(price) if price is not None else None,'price_provenance':'explicit_recommendation_text' if price is not None else 'missing',
             'price_approximate':approx,'recommendation_extract':text,'parse_rule':tag,
             'period':'player_prop' if market=='player_prop' else 'partial_or_team_total' if partial else 'full_game',
             'period_evidence':'explicit_recommendation_full_game_default; non-full-game labels excluded',
             'source_reported_published_at':published,'source_reported_modified_at':meta.get('dateModified'),
             'observed_at':doc['observed_at'],'document_sha256':doc['sha256'],
             'provenance_request_url':doc.get('provenance_request_url',url),'cutoff_proven':False,
             'evidence_class':'retrospective_source_claim_not_pregame_certified','exclusions':reasons}
        row['prediction_id']=hashlib.sha256(json.dumps([url,league,dt,teams,market,selection,line,price,row['author']],ensure_ascii=False).encode()).hexdigest()[:24]
        rows.append(row)
    # Exact extracted duplicate within one article only.
    return list({r['prediction_id']:r for r in rows}.values())

def rehydrate():
    """Re-extract short facts from local response cache, with no HTTP requests."""
    docs={}
    entries=[]
    for path in (OUT/'cache').glob('*.json.gz'):
        with gzip.open(path,'rt',encoding='utf-8') as f:entries.append(json.load(f))
    for e in sorted(entries,key=lambda e:e['observed_at']):
        body=e.get('body','');u=e['url']
        if '/wp-json/wp/v2/posts?' in u and body:
            try:posts=json.loads(body)
            except ValueError:continue
            if not isinstance(posts,list):continue
            for p in posts:
                if 'content' in p:docs[p['link']]=document(p['link'],p['content']['rendered'],e,p)
        elif body and urlparse(u).hostname in ('www.pickswise.com','www.covers.com') and '.xml' not in u:
            docs[u]=document(u,body,e)
    save('documents.json',docs);build()
    print('rehydrated documents',len(docs))

def months():
    cur=date(2025,10,1)
    while cur<=date(2026,10,1):
        nxt=(cur.replace(day=28)+timedelta(days=4)).replace(day=1)
        yield max(cur.isoformat(),START),min(nxt.isoformat(),'2026-10-28')
        cur=nxt
def sitemap(url,depth=0):
    if depth>3:return []
    e=fetch(url)
    if not e.get('body'):return []
    try:root=ET.fromstring(e['body'])
    except ET.ParseError:return []
    out=[]
    for n in root:
        loc=n.find('{*}loc')
        if loc is None:continue
        u=loc.text
        if root.tag.endswith('sitemapindex'):out+=sitemap(u,depth+1)
        else:
            d=n.find('{*}lastmod');p=n.find('.//{*}publication_date')
            out.append({'url':u,'date':p.text if p is not None else d.text if d is not None else None})
    return out

def collect():
    OUT.mkdir(parents=True,exist_ok=True)
    docs=read('documents.json',{});discovery=read('discovery.json',[])
    authors=read('authors.json',{})
    for lo,hi in months():
        for league,params in [('MLB',{'categories':4241}),('NBA',{'categories':3008}),('NPB',{'search':'NPB'})]:
            q={'per_page':25,'order':'asc','orderby':'date','after':lo+'T00:00:00','before':hi+'T00:00:00',
               '_fields':'id,date_gmt,modified_gmt,link,title,content,author',**params}
            u='https://www.tonyspicks.com/wp-json/wp/v2/posts?'+urlencode(q)
            e=fetch(u)
            posts=json.loads(e['body']) if e.get('body') else []
            discovery.append({'source':'TonysPicks','league_query':league,'month':lo[:7],'url':u,'available':e.get('total'),'sampled':len(posts),'status':e.get('status')})
            for p in posts:
                docs[p['link']]=document(p['link'],p['content']['rendered'],e,p)
            save('documents.json',docs);save('discovery.json',discovery)
        print('archive',lo[:7],'documents',len(docs),flush=True)
        build()
    for aid in sorted({d.get('wp_author_id') for d in docs.values() if d.get('wp_author_id')}):
        if str(aid) in authors:continue
        e=fetch(f'https://www.tonyspicks.com/wp-json/wp/v2/users/{aid}?_fields=id,name,slug')
        if e.get('body'):authors[str(aid)]=json.loads(e['body']).get('name')
    save('authors.json',authors)
    discovered=[]
    for u in ['https://www.pickswise.com/mlb/news/sitemap.xml','https://www.pickswise.com/nba/news/sitemap.xml',
              'https://www.pickswise.com/news/sitemap.xml','https://www.covers.com/editorial-sitemap.xml']:
        discovered+=sitemap(u)
    save('sitemap_urls.json',discovered)
    groups=defaultdict(list)
    for r in discovered:
        u=r['url'];d=r.get('date') or ''
        if not START<=d[:10]<=END:continue
        league='MLB' if re.search(r'/mlb/|mlb-',u) else 'NBA' if re.search(r'/nba/|nba-',u) else None
        if not league or not re.search(r'pick|prediction|best-bet',u):continue
        if re.search(r'parlay|promo|bonus|fantasy|dfs|home-run|player-prop',u):continue
        groups[(urlparse(u).hostname,league,d[:7])].append(r)
    selected=[]
    for key,rows in sorted(groups.items()):
        ordered=sorted({r['url']:r for r in rows}.values(),key=lambda r:(r['date'],r['url']))
        selected+=ordered[:18]
        discovery.append({'source':key[0],'league_query':key[1],'month':key[2],'available':len(ordered),'sampled':min(18,len(ordered))})
    save('selected_urls.json',selected);save('discovery.json',discovery)
    for i,r in enumerate(selected):
        if r['url'] not in docs:
            e=fetch(r['url'])
            if e.get('body'):docs[r['url']]=document(r['url'],e['body'],e)
        if i%15==0:
            save('documents.json',docs);build();print('news',i,len(selected),'docs',len(docs),flush=True)
    save('documents.json',docs);build();official();build()

def official():
    # One official MLB schedule response per calendar month. Never use source scoreboards.
    matches={r['official_id']:r for r in read('official_matches.json',[])}
    for lo,hi in months():
        last=(date.fromisoformat(hi)-timedelta(days=1)).isoformat()
        u=f'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={lo}&endDate={last}'
        e=fetch(u)
        if not e.get('body'):continue
        for day in json.loads(e['body']).get('dates',[]):
            for g in day.get('games',[]):
                if g.get('status',{}).get('abstractGameState')!='Final':continue
                a=g['teams']['away'];h=g['teams']['home']
                if a.get('score') is None or h.get('score') is None:continue
                k='MLB:'+str(g['gamePk'])
                matches[k]={'official_id':k,'league':'MLB','date':g['officialDate'],'start_at':g['gameDate'],
                            'away':a['team']['name'],'home':h['team']['name'],'away_score':a['score'],'home_score':h['score'],
                            'status':g['status'],'game_number':g.get('gameNumber'),'double_header':g.get('doubleHeader'),
                            'official_url':u,'observed_at':e['observed_at'],'sha256':e['sha256']}
        save('official_matches.json',list(matches.values()))
    # NBA access is handled by the parent task after standard endpoints returned
    # 403; do not duplicate that work or substitute a source scoreboard.
    npb_codes=dict(zip(['g','s','db','d','t','c','f','e','h','b','m','l'],NPB))
    dates=sorted({r['match_date'] for d in read('documents.json',{}).values()
                  for r in parse(d,read('authors.json',{})) if r['league']=='NPB'})
    for dt in dates:
        u=f'https://npb.jp/bis/{dt[:4]}/games/gm{dt.replace("-","")}.html'
        e=fetch(u)
        if not e.get('body'):continue
        s=BeautifulSoup(e['body'],'html.parser')
        for a in s.select('a[href]'):
            href=a['href'];c=re.search(r'/scores/\d{4}/\d{4}/([a-z]+)-([a-z]+)-\d+/',href)
            if not c or c[1] not in npb_codes or c[2] not in npb_codes:continue
            score=re.match(r'(.+?) (\d+) (\d+)回戦 .+? (\d+) (.+)$',clean(a.get_text(' ',strip=True)))
            if not score:continue  # postponed, live, malformed and unscored cards excluded
            k='NPB:'+href
            matches[k]={'official_id':k,'league':'NPB','date':dt,'start_at':None,
                        'home':npb_codes[c[1]],'away':npb_codes[c[2]],'home_score':int(score[2]),'away_score':int(score[4]),
                        'status':'final_scored_official_daily_card','official_url':u,'official_game_url':'https://npb.jp'+href,
                        'official_score_extract':clean(a.get_text(' ',strip=True)),
                        'observed_at':e['observed_at'],'sha256':e['sha256']}
        save('official_matches.json',list(matches.values()))

def settle(row,game):
    market=row['market'];sel=row['selection'];line=row['line']
    if market=='total':margin=(game['away_score']+game['home_score']-line)*(1 if sel=='over' else -1)
    elif market in ('moneyline','spread'):
        if sel not in (game['away'],game['home']):return None
        margin=(game['away_score']-game['home_score'])*(1 if sel==game['away'] else -1)
        if market=='spread':margin+=line
        if market=='moneyline' and margin==0:return 'unresolved_tie_rules'
    else:return None
    return 'win' if margin>0 else 'loss' if margin<0 else 'push'
def build():
    docs=read('documents.json',{});authors=read('authors.json',{})
    preds={}
    for d in docs.values():
        for r in parse(d,authors):preds[r['prediction_id']]=r
    rows=sorted(preds.values(),key=lambda r:(r['match_date'],r['source'],r['url'],r['prediction_id']))
    games=read('official_matches.json',[]);index=defaultdict(list)
    for g in games:
        g['official_result_url']=g['official_url'];g['source_url']=g['official_url']
        g['official_result_id']=g['official_id'];g['match_date']=g['date']
        g['result_provenance']='official_public_response'
        g['official_status']='final'
        if g['league']=='MLB':g['gamePk']=int(g['official_id'].split(':',1)[1])
    save('official_matches.json',games)
    for g in games:index[(g['league'],g['date'],frozenset([g['away'],g['home']]))].append(g)
    settlements=[]
    for r in rows:
        r['fixture_key']=r['league']+':'+r['match_date']+':'+ '|'.join(sorted(r['teams']))
    seen={}
    for r in sorted(rows,key=lambda r:(r['source_reported_published_at'] or '',r['url'],r['prediction_id'])):
        key=(r['source'],r['author'],r['fixture_key'],r['market'],r['selection'],r['line'],r['original_american_odds'])
        if key in seen:
            r['duplicate_of']=seen[key];r['exclusions'].append('same_source_author_fixture_selection_price_duplicate')
        else:seen[key]=r['prediction_id']
    for r in rows:
        candidates=index.get((r['league'],r['match_date'],frozenset(r['teams'])),[])
        reason='official_result_unavailable' if not candidates else 'ambiguous_doubleheader' if len(candidates)>1 else None
        if len(candidates)==1 and r['market'] in ('moneyline','total','spread') and r['selection'] and not set(r['exclusions']).intersection({'partial_game_or_team_total_not_full_game','fixture_context_mismatch'}):
            g=candidates[0];outcome=settle(r,g)
            if outcome:
                settlements.append({'prediction_id':r['prediction_id'],'official_id':g['official_id'],'fixture_key':r['fixture_key'],
                    'result':outcome,'priced_eligible':not r['exclusions'],
                    'net_units_flat_stake':(r['decimal_odds']-1 if outcome=='win' else -1 if outcome=='loss' else 0) if not r['exclusions'] and outcome in ('win','loss','push') else None})
                r['official_id']=g['official_id']
        r['official_match_gap']=reason
    jsonl('predictions.jsonl',rows);jsonl('settlements.jsonl',settlements)
    official_by_id={g['official_id']:g for g in games}
    normalized=[]
    for r in rows:
        g=official_by_id.get(r.get('official_id'))
        side=r['selection'] if r['market']=='total' else ('home' if g and r['selection']==g['home'] else 'away' if g and r['selection']==g['away'] else None)
        normalized.append({**r,'source_url':r['url'],'selected_team':r['selection'] if r['market'] in ('moneyline','spread') else None,
                           'side':side,'reported_decimal_odds':r['decimal_odds'],
                           'odds_time_quality':'original_recommendation_claim' if r['decimal_odds'] is not None and not r['price_approximate'] else 'approximate_recommendation_claim' if r['price_approximate'] else 'missing',
                           'full_game_period_explicit':bool(re.search(r'full.game|full.time',r['recommendation_extract'],re.I))})
    jsonl('candidates-normalized.jsonl',normalized)
    coverage={}
    settlement_by_id={s['prediction_id']:s for s in settlements}
    for month in [lo[:7] for lo,_ in months()]:
        part=[r for r in rows if r['match_date'].startswith(month)]
        coverage[month]={'picks':len(part),'unique_fixtures':len({r['fixture_key'] for r in part}),
                         'priced_picks':sum(not r['exclusions'] for r in part),'by_source':dict(Counter(r['source'] for r in part)),
                         'by_league':dict(Counter(r['league'] for r in part)),
                         'official_matched_picks':sum(r['prediction_id'] in settlement_by_id for r in part),
                         'priced_official_matched_picks':sum(r['prediction_id'] in settlement_by_id and not r['exclusions'] for r in part)}
    summary={'generated_at':now(),'target':[START,END],'documents':len(docs),'picks':len(rows),
             'unique_fixtures':len({r['fixture_key'] for r in rows}),
             'distinct_named_authors':len({r['author'] for r in rows if r['author']}),
             'distinct_individual_author_labels':len({r['author'] for r in rows if r['author'] and 'staff' not in r['author'].lower()}),
             'unknown_author_picks':sum(not r['author'] for r in rows),
             'by_source':dict(Counter(r['source'] for r in rows)),'by_league':dict(Counter(r['league'] for r in rows)),
             'named_authors':dict(Counter(r['author'] for r in rows if r['author'])),
             'priced_eligible_picks':sum(not r['exclusions'] for r in rows),
             'resolved_full_game_direction_picks':sum(r['market'] in ('moneyline','total','spread') and r['selection'] is not None and r['period']=='full_game' and not r.get('duplicate_of') for r in rows),
             'official_matched_picks':len(settlements),'official_matched_unique_fixtures':len({s['official_id'] for s in settlements}),
             'outcomes_all_matched':dict(Counter(s['result'] for s in settlements)),
             'priced_outcomes':dict(Counter(s['result'] for s in settlements if s['priced_eligible'])),
             'exclusions':dict(Counter(x for r in rows for x in r['exclusions'])),
             'months_with_picks':sum(v['picks']>0 for v in coverage.values()),
             'actual_date_range':[min((r['match_date'] for r in rows),default=None),max((r['match_date'] for r in rows),default=None)],
             'cutoff_proven_picks':0,'statistical_validation_owner':'parent task','month_coverage':coverage}
    summary['by_source_league']={}
    for source,league in sorted({(r['source'],r['league']) for r in rows}):
        part=[r for r in rows if r['source']==source and r['league']==league]
        summary['by_source_league'][source+':'+league]={'candidates':len(part),'unique_fixtures':len({r['fixture_key'] for r in part}),
            'priced_eligible':sum(not r['exclusions'] for r in part),'official_matched':sum(r['prediction_id'] in settlement_by_id for r in part),
            'priced_official_matched':sum(r['prediction_id'] in settlement_by_id and not r['exclusions'] for r in part)}
    save('summary.json',summary)
    return summary

def check():
    def fake(m,s,l=None):return {'market':m,'selection':s,'line':l}
    g={'away':'A','home':'B','away_score':5,'home_score':3}
    assert settle(fake('moneyline','A'),g)=='win'
    assert settle(fake('moneyline','B'),g)=='loss'
    assert settle(fake('spread','A',-2),g)=='push'
    assert settle(fake('spread','B',2.5),g)=='win'
    assert settle(fake('total','over',8),g)=='push'
    assert settle(fake('total','under',8.5),g)=='win'
    assert settle(fake('total','over',8.5),g)=='loss'
    assert abs(decimal(-150)-5/3)<1e-12 and decimal(125)==2.25
    assert len(teams_in('LA Dodgers vs. NY Yankees','MLB'))==2
    assert teams_in('Boston Red Sox vs. Atlanta Braves','MLB')==['Boston Red Sox','Atlanta Braves']
    assert resolve_selection('Play LA -137',['Los Angeles Dodgers','Toronto Blue Jays'],'MLB')=='Los Angeles Dodgers'
    assert resolve_selection('Play LA -5',['Los Angeles Lakers','Los Angeles Clippers'],'NBA') is None
    assert match_date('Free picks May 13, 2026','2026-05-12T23:00:00Z')=='2026-05-13'
    def example(text,league='NBA',title='Knicks vs Bucks NBA picks',tag='h2'):
        return parse({'url':'https://www.pickswise.com/'+league.lower()+'/test','source':'Pickswise',
            'title':title,'metadata':{'datePublished':'2026-05-01T10:00:00Z','author':{'name':'Test Author'}},
            'observed_at':now(),'sha256':'test','nodes':[{'tag':tag,'text':text}]},{})
    x=example('Knicks vs Bucks best bet: Bucks ML (+104)')[0]
    assert x['selection']=='Milwaukee Bucks' and x['market']=='moneyline' and x['original_american_odds']==104
    x=example('Knicks vs Bucks best bet: Knicks +4.5 (-110)')[0]
    assert x['selection']=='New York Knicks' and x['line']==4.5 and x['market']=='spread'
    x=example('Knicks vs Bucks best bet: Over 225.5 (-105)')[0]
    assert x['selection']=='over' and x['line']==225.5 and x['market']=='total'
    x=example('Knicks vs Bucks best bet: Bucks ML (around -120)')[0]
    assert 'approximate_price' in x['exclusions']
    x=example('Knicks vs Bucks best bet: Knicks +4.5')[0]
    assert x['decimal_odds'] is None and 'original_price_missing_or_ambiguous' in x['exclusions']
    assert example('Score Prediction: Knicks 115, Bucks 110')==[]
    rows=[json.loads(x) for x in (OUT/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    settlements=[json.loads(x) for x in (OUT/'settlements.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len({r['prediction_id'] for r in rows})==len(rows)
    assert all(START<=r['match_date']<=END and not r['cutoff_proven'] for r in rows)
    assert all(not r['price_approximate'] for r in rows if not r['exclusions'])
    assert all(s['official_id'].startswith(('MLB:','NPB:','NBA:')) for s in settlements)
    by_id={r['prediction_id']:r for r in rows};officials={g['official_id']:g for g in read('official_matches.json',[])}
    for st in settlements:
        r=by_id[st['prediction_id']];g=officials[st['official_id']]
        assert frozenset(r['teams'])==frozenset([g['away'],g['home']]) and r['match_date']==g['date']
        if st['priced_eligible']:
            assert not r['exclusions'] and r['decimal_odds']>1 and r['period']=='full_game'
            assert not r.get('duplicate_of')
        if r['market']=='total':
            score=g['away_score']+g['home_score']
            expected='push' if score==r['line'] else 'win' if (score>r['line'])==(r['selection']=='over') else 'loss'
        else:
            selected=g['home_score'] if r['selection']==g['home'] else g['away_score']
            opponent=g['away_score'] if r['selection']==g['home'] else g['home_score']
            adjusted=selected+(r['line'] if r['market']=='spread' else 0)
            expected='push' if adjusted==opponent else 'win' if adjusted>opponent else 'loss'
            if r['market']=='moneyline' and selected==opponent:expected='unresolved_tie_rules'
        assert st['result']==expected
    save('verification.json',{'checked_at':now(),'status':'passed','checks':['moneyline win/loss','spread push and side','total win/loss/push','American conversion','Red Sox overlapping alias regression','ambiguous LA rejected','claimed fixture date versus publication','best-bet matchup prefix separated from selected team','spread and total recommendation parsers','approximate and missing odds excluded','score projection not a betting pick','unique prediction IDs','target dates','retrospective flags','all official fixture/date joins','independent score-based re-settlement of every matched pick','semantic duplicates not priced eligible'],'rows':len(rows),'settlements':len(settlements)})
    print('CHECK PASSED',len(rows),len(settlements))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['collect','build','official','check','rehydrate']);a=p.parse_args()
    globals()[a.command]()
