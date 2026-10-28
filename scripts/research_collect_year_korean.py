"""Public Korean archive capture, source claims only; never used by production.

Discovery is independent of outcomes. Cached parsing facts enable offline replay.
Only short structural/pick evidence is saved, not complete articles. No retries,
proxy rotation, challenge bypass or login. One request at a time, >=0.55s apart.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/tipster-year-20261028/korean'
START, END = '2025-10-28', '2026-10-27'
ALIASES = {
 'KBO': {'LG 트윈스':['LG 트윈스','LG'], '두산 베어스':['두산 베어스','두산'], '삼성 라이온즈':['삼성 라이온즈','삼성 라이온스','삼성'], 'KIA 타이거즈':['KIA 타이거즈','기아','KIA'], '한화 이글스':['한화 이글스','한화'], 'KT 위즈':['KT 위즈','KT','kt'], 'SSG 랜더스':['SSG 랜더스','SSG'], 'NC 다이노스':['NC 다이노스','NC'], '롯데 자이언츠':['롯데 자이언츠','롯데'], '키움 히어로즈':['키움 히어로즈','키움']},
 'NPB': {'한신 타이거스':['한신 타이거스','한신'], '요미우리 자이언츠':['요미우리 자이언츠','요미우리'], '주니치 드래곤즈':['주니치 드래곤즈','주니치'], '히로시마 도요 카프':['히로시마 도요 카프','히로시마'], '요코하마 DeNA 베이스타스':['요코하마 DeNA 베이스타스','요코하마 DeNA','요코하마','DeNA','데이나'], '야쿠르트 스왈로즈':['야쿠르트 스왈로즈','야쿠르트'], '니혼햄 파이터스':['니혼햄 파이터스','닛폰햄 파이터스','닛폰햄','니혼햄','니혼함'], '소프트뱅크 호크스':['소프트뱅크 호크스','소프트뱅크'], '오릭스 버팔로스':['오릭스 버팔로스','오릭스'], '라쿠텐 골든이글스':['라쿠텐 골든이글스','라쿠텐'], '세이부 라이온즈':['세이부 라이온즈','세이부'], '지바 롯데 마린스':['지바 롯데 마린스','지바롯데','지바 롯데','치바롯데','치바 롯데']},
 'KLeague': {'FC서울':['FC서울','FC 서울','서울 FC'], '울산HD':['울산HDFC','울산 HD','울산현대','울산'], '포항스틸러스':['포항스틸','포항 스틸러스','포항'], '전북현대':['전북현대','전북 현대','전북'], '강원FC':['강원FC','강원 FC','강원'], '광주FC':['광주FC','광주 FC','광주'], '대전하나':['대전하나','대전 하나','대전'], '김천상무':['김천상무','김천 상무','김천'], 'FC안양':['FC안양','FC 안양','안양'], '제주SK':['제주SKFC','제주 SK','제주 Utd','제주 유나이티드','제주'], '부천FC':['부천FC','부천 FC','부천'], '인천유나이티드':['인천유나','인천 유나이티드','인천'], '수원FC':['수원FC','수원 FC'], '수원삼성':['수원삼성','수원 삼성'], '부산아이파크':['부산 아이파크','부산IPark','부산'], '서울이랜드':['서울 이랜드','서울이랜드','이랜드','서울E'], '김포FC':['김포FC','김포'], '전남드래곤즈':['전남 드래곤즈','전남'], '충남아산':['충남 아산','충남아산','아산'], '성남FC':['성남FC','성남'], '천안시티':['천안시티','천안'], '안산그리너스':['안산 그리너스','안산'], '충북청주':['충북청주','충북 청주','청주'], '경남FC':['경남FC','경남'], '화성FC':['화성FC','화성 FC','화성'], '용인FC':['용인 시티','용인'], '김해FC':['김해FC','김해'], '파주FC':['파주FC','파주']}}
K1_2026 = {'FC서울','울산HD','포항스틸러스','전북현대','강원FC','광주FC','대전하나','김천상무','FC안양','제주SK','부천FC','인천유나이티드'}
ALIASES['KBL']={'창원 LG':['창원 LG','창원LG'], '서울 삼성':['서울 삼성','서울삼성'], '서울 SK':['서울 SK','서울SK','SK나이츠'], '안양 정관장':['안양 정관장','안양정관장','정관장'], '원주 DB':['원주 DB','원주DB'], '부산 KCC':['부산 KCC','부산KCC','KCC'], '고양 소노':['고양 소노','고양소노','소노'], '대구 한국가스공사':['대구 한국가스공사','대구한국가스공사','한국가스공사','가스공사'], '울산 현대모비스':['울산 현대모비스','울산현대모비스','현대모비스'], '수원 KT':['수원 KT','수원KT']}
ALIASES['KLeague']['대구FC']=['대구FC','대구 FC']
ALIASES['KLeague']['수원삼성'].append('수원S')


def now():
    return datetime.now(timezone.utc).isoformat()


def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def read(name, default):
    p=OUT/name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def append(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/name).open('a',encoding='utf-8') as f:
        f.write(json.dumps(obj,ensure_ascii=False)+'\n')


def norm(s):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',s)).strip()


def fetch(url,purpose):
    time.sleep(.55)
    t=now()
    try:
        r=requests.get(url,timeout=14)
        meta=dict(url=url,final_url=r.url,purpose=purpose,request_started_at=t,
                  observed_at=now(),status=r.status_code,response_bytes=len(r.content),
                  sha256=hashlib.sha256(r.content).hexdigest())
        append('transport.jsonl',meta)
        if r.status_code!=200:
            return None,meta
        return r.content,meta
    except requests.RequestException as e:
        meta=dict(url=url,purpose=purpose,observed_at=now(),error=type(e).__name__,detail=str(e)[:220])
        append('transport.jsonl',meta)
        return None,meta


def teams_in(text, league):
    # Longest aliases first prevent 지바 롯데 from becoming Korean 롯데.
    mapping={norm(a).lower():team for team,aa in ALIASES[league].items() for a in aa}
    pat='|'.join(re.escape(a) for a in sorted(mapping,key=len,reverse=True))
    found=[]
    for m in re.finditer(pat,norm(text).lower()):
        a=m.group(); before=norm(text).lower()[max(0,m.start()-1):m.start()]
        after=norm(text).lower()[m.end():m.end()+1]
        if a.isascii() and (before.isalpha() or after.isalpha()):
            continue
        team=mapping[a]
        if team not in found: found.append(team)
    return found


def matchup(text):
    if not re.search(r'\bvs\b|[–—]|(?:\S\s+-\s+\S)|대\s|대결|맞대결',text,re.I):
        return None
    for league in ['NPB','KBL','KBO','KLeague']:
        if league=='NPB' and re.search(r'마리노스|산프레체|요코하마FC|J리그|J1|J2',text,re.I): continue
        ts=teams_in(text,league)
        if len(ts)==2:
            if league=='KLeague':
                league='K2' if re.search(r'K리그\s*2|K2',text,re.I) else 'K1' if re.search(r'K리그\s*1|K1',text,re.I) or set(ts)<=K1_2026 else 'K2'
            return league,ts
    return None


def get_date(text, year='2026'):
    for pat in [r'(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})',r'(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일',r'(?<!\d)(\d{1,2})/(\d{1,2})(?:일|\b)']:
        m=re.search(pat,text)
        if m:
            vals=list(m.groups())
            if len(vals)==2: vals=[year]+vals
            try: return date(*map(int,vals)).isoformat()
            except ValueError: pass
    return None


def discover():
    inventory={x['url']:x for x in read('discovered.json',[])}
    for host in ['sportsanalysis1.com','golfmasterj.tistory.com']:
        data,meta=fetch('https://'+host+'/sitemap.xml','sitemap_discovery')
        if not data: continue
        entries=[]
        for e in ET.fromstring(data):
            u,d=e.findtext('{*}loc'),e.findtext('{*}lastmod')
            if '/entry/' not in (u or ''): continue
            title=unquote(u.split('/entry/')[-1]).replace('-',' ')
            if not re.search(r'KBO|NPB|K리그|국야|일야|프로야구',title,re.I): continue
            entries.append(dict(url=u,title_hint=title,sitemap_lastmod_claim=d,observed_at=meta['observed_at']))
            if d and START<=d[:10]<=END:
                inventory[u]=entries[-1]
        save(host.split('.')[0]+'_sitemap_inventory.json',entries)
        print(host,'relevant sitemap entries',len(entries),flush=True)
    base='https://guidesyj.tistory.com/category/*스포츠경기분석*/2.야구'
    page,maxpage=1,1
    while page<=maxpage:
        data,meta=fetch(base+f'?page={page}','all_baseball_category_pages')
        if data:
            soup=BeautifulSoup(data,'html.parser')
            for a in soup.select('.pagination a[href], .paging a[href]'):
                m=re.search(r'page=(\d+)',a['href'])
                if m: maxpage=max(maxpage,int(m[1]))
            for a in soup.select('a[href]'):
                if not re.fullmatch(r'/\d+',a.get('href','')): continue
                title=norm(a.get_text(' ',strip=True))[:230]
                if not matchup(title):
                    continue
                u=urljoin(base,a['href'])
                inventory[u]=dict(url=u,title_hint=title,listing_url=base+f'?page={page}',observed_at=meta['observed_at'])
        if page%5==0: print('guide listing',page,'of',maxpage,'inventory',len(inventory),flush=True)
        page+=1
        save('discovered.json',sorted(inventory.values(),key=lambda x:(get_date(x['title_hint']) or x.get('sitemap_lastmod_claim') or '',x['url'])))
    print('discovered total',len(inventory),flush=True)


def prepare_inventory():
    inventory={x['url']:x for x in read('discovered.json',[])}
    for p in OUT.glob('*_supplemental_inventory.json'):
        for row in json.loads(p.read_text(encoding='utf-8')): inventory[row['url']]=row
    eligible=[];excluded=[]
    for row in inventory.values():
        hint=get_date(row['title_hint'])
        if hint and not START<=hint<=END:
            excluded.append(dict(**row,reason='listing_match_date_outside_target',match_date=hint))
        else: eligible.append(row)
    save('listing_exclusions.json',excluded)
    save('discovered.json',sorted(eligible,key=lambda x:(get_date(x['title_hint']) or x.get('sitemap_lastmod_claim') or '',x['url'])))
    print('eligible inventory',len(eligible),'listing exclusions',len(excluded),flush=True)


def capture():
    done={r['source_url'] for r in read('documents.json',[])}
    docs=read('documents.json',[])
    # Month round-robin, chronological within month. This gives a useful coverage
    # checkpoint if interrupted, while the intended endpoint remains all articles.
    buckets=defaultdict(list)
    for row in read('discovered.json',[]):
        md=get_date(row['title_hint']) or row.get('sitemap_lastmod_claim') or ''
        buckets[md[:7]].append(row)
    ordered=[]
    while any(buckets.values()):
        for month in sorted(buckets):
            if buckets[month]: ordered.append(buckets[month].pop(0))
    for ix,row in enumerate(ordered):
        u=row['url']
        if u in done: continue
        data,meta=fetch(u,'article_pick_capture')
        if not data:
            append('excluded_documents.jsonl',dict(source_url=u,reason='fetch_failed',observed_at=meta['observed_at']))
            continue
        soup=BeautifulSoup(data,'html.parser'); body=soup.select_one('.contents_style') or soup.select_one('.entry-content')
        mm={m.get('property') or m.get('name'):m.get('content') for m in soup.select('meta[content]')}
        title=mm.get('og:title') or (soup.title.get_text() if soup.title else row['title_hint'])
        pub=mm.get('article:published_time');author=mm.get('author') or mm.get('og.article.author') or mm.get('article:author')
        if not body:
            append('excluded_documents.jsonl',dict(source_url=u,reason='body_not_found',observed_at=meta['observed_at']));continue
        # Persist structure and compact factual recommendation evidence only.
        units=[]
        for el in body.select('h1,h2,h3,h4,h5,p,li,tr'):
            if el.name!='tr' and el.find_parent(['tr','li']): continue
            text=norm(el.get_text(' ',strip=True)).replace('**','')
            if not text: continue
            if el.name=='tr':
                cells=[norm(c.get_text(' ',strip=True))[:280] for c in el.find_all(['td','th'],recursive=False)]
                units.append(dict(tag='tr',text=' | '.join(cells)[:1000],cells=cells));continue
            if (el.name.startswith('h') or re.search(r'vs|승패|승무패|승/패|승/무/패|승리|우세|추천|예측|예상|경기일|일정:|방향|홈승|원정승|배당|언더|오버|핸디|[–—]',text,re.I)):
                # Long paragraphs may contain literal Markdown sections. Split those.
                chunks=re.split(r'(?=###\s*\[|###\s*경기)',text)
                for chunk in chunks:
                    if len(chunk)>1000:
                        # Extract matchup/explicit-field lines from literal Markdown.
                        for m in re.finditer(r'(?:###\s*[^#]{0,180}|(?:승무패|승패|언더/오버)\s*예측\s*:[^-]{1,130})',chunk):
                            units.append(dict(tag=el.name,text=m[0][:280]))
                    else: units.append(dict(tag=el.name,text=chunk[:1000]))
        doc=dict(source_url=u,source=urlparse(u).netloc,title=title,published_at_claim=pub,
                 author=author,observed_at=meta['observed_at'],sha256=meta['sha256'],
                 match_date_hint=get_date(title, (pub or '2026')[:4]) or get_date(norm(body.get_text(' ',strip=True))[:450],(pub or '2026')[:4]),
                 units=units)
        docs.append(doc);done.add(u);save('documents.json',docs)
        if len(docs)%25==0:
            print('captured',len(docs),'of',len(read('discovered.json',[])),flush=True)
            build()


def direction(text,league,teams):
    group='KLeague' if league in ['K1','K2'] else league
    s=norm(text)
    if re.search(r'보류|관망|패스|혼전|배제|피하|추천하지|추천 안|취소',s): return None
    if s=='승': return 'home',teams[0]
    if s=='패': return 'away',teams[1]
    if re.search(r'홈(?:팀)?\s*승',s): return 'home',teams[0]
    if re.search(r'원정(?:팀)?\s*승|어웨이\s*승',s): return 'away',teams[1]
    if re.search(r'무승부|^무$|\bDRAW\b',s,re.I): return 'draw',None
    ts=teams_in(s,group)
    if league=='KBL' and not ts:
        ts=[t for t in teams if re.search(r'(?<![A-Za-z])'+re.escape(t.split()[-1])+r'\s*(?:승|우세)',s,re.I)]
    if len(ts)==1 and ts[0] in teams and re.search(r'승|우세',s): return ('home' if ts[0]==teams[0] else 'away'),ts[0]
    return None


def parse_doc(doc):
    rows=[]; excluded=[];title_fixture=matchup(doc['title']);context=title_fixture;md=doc['match_date_hint']; headers=[];final_section=False
    def emit(fixture,when,market,side,team,evidence,line=None,odds=None,method='explicit_field'):
        if not fixture or not when: return
        league,teams=fixture
        rec=dict(source=doc['source'],source_url=doc['source_url'],author=doc['author'],
                 published_at_claim=doc['published_at_claim'],source_reported_published_at=doc['published_at_claim'],
                 source_timezone='Asia/Seoul' if doc['published_at_claim'] and '+09:00' in doc['published_at_claim'] else None,
                 observed_at=doc['observed_at'],source_sha256=doc['sha256'],match_date=when,
                 league=league,teams=teams,home=teams[0],away=teams[1],teams_order_semantics='source_order_not_officially_verified',
                 market=market,side=side,selected_team=team,line=line,original_odds=odds,reported_decimal_odds=odds,
                 odds_time_quality='source_claim_unverified_timestamp' if odds else 'not_reported',
                 evidence_excerpt=evidence[:160],extraction_method=method,cutoff_proven=False,
                 pregame_evidence='source_date_claim_only; first_observed_postgame',score=None,official_url=None)
        if not START<=when<=END: rec['exclusion_reason']='match_date_outside_target';excluded.append(rec)
        elif doc['published_at_claim'] and doc['published_at_claim'][:10]>when:
            rec['exclusion_reason']='claimed_publication_after_match_date';excluded.append(rec)
        else: rows.append(rec)
    for unit in doc['units']:
        s=unit['text']; fixture=matchup(s)
        if unit['tag'].startswith('h'):
            final_section=bool(re.search(r'최종.*예측|최종.*결론|추천 픽|승패 예측',s))
        # Fixture headings change context; summary rows use their own matchup.
        if fixture and unit['tag']!='tr' and (not title_fixture or fixture==title_fixture) and (unit['tag'].startswith('h') or re.match(r'^(?:선발|경기\s*[:：])',s) and len(s)<220):
            context=fixture
        if unit['tag']=='tr':
            cells=unit['cells']
            if context and len(cells)>=2 and re.search(r'^승패|^승무패|^90분 방향',cells[0]):
                d=direction(cells[1],context[0],context[1])
                if d: emit(context,md,'moneyline',d[0],d[1],s,method='label_value_table')
                else: excluded.append(dict(source_url=doc['source_url'],author=doc['author'],match_date=md,league=context[0],teams=context[1],reason='ambiguous_or_pass_prediction',evidence_excerpt=s[:160]))
                continue
            if context and len(cells)>=2 and re.search(r'핸디캡|언더.?오버|오버.?언더|총점',cells[0]):
                value=cells[1]
                tm=re.search(r'(언더|오버)\s*\(?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*\)?\s*(언더|오버)',value)
                if tm and not re.search(r'보류|패스|관망',value):
                    side='under' if (tm[1] or tm[4])=='언더' else 'over';line=float(tm[2] or tm[3])
                    emit(context,md,'total',side,None,s,line,method='label_value_table')
                else: excluded.append(dict(source_url=doc['source_url'],author=doc['author'],match_date=md,league=context[0],teams=context[1],reason='unsupported_market_or_missing_line',evidence_excerpt=s[:160]))
                continue
            if any(re.search(r'예측|예상|전망|추천|방향|선택|밸류|^픽$',c) for c in cells) and not fixture:
                if context and len(cells)==2 and re.search(r'^승패|^승무패|90분 방향',cells[0]):
                    d=direction(cells[1],context[0],context[1])
                    if d: emit(context,md,'moneyline',d[0],d[1],s,method='label_value_table')
                headers=cells;continue
            if not fixture: continue
            league,teams=fixture
            when=get_date(s,(md or '2026')[:4]) or md
            # Only explicitly labelled forecast columns, never H2H score tables.
            for j,h in enumerate(headers):
                if j>=len(cells) or not re.search(r'예측|예상|전망|추천|방향|선택|밸류|^픽$',h): continue
                value=cells[j];d=direction(value,league,teams)
                if d and not re.search(r'핸디|언더|오버|\+\d|-\d|무패|승/무|승무',value):
                    odds=None
                    for oi,oh in enumerate(headers):
                        if oi<len(cells) and '배당' in oh and re.search(r'승.*무.*패|1X2',oh,re.I):
                            prices=re.findall(r'(?<!\d)(\d+\.\d+)',cells[oi])
                            if len(prices)==3 and all(float(p)>1 for p in prices): odds=float(prices[{'home':0,'draw':1,'away':2}[d[0]]])
                    emit(fixture,when,'moneyline',d[0],d[1],s,odds=odds,method='labelled_forecast_table')
            continue
        if not context: continue
        league,teams=context
        conclusion=re.match(r'결론\s*[→:：]\s*([^+.]{1,80})',s)
        if conclusion and not re.search(r'F5|5회|전반|핸디',conclusion[1],re.I):
            d=direction(conclusion[1],league,teams)
            if d: emit(context,md,'moneyline',d[0],d[1],conclusion[0],method='explicit_conclusion')
        if final_section and unit['tag'] in ['p','li'] and len(s)<160 and not re.search(r'핸디|언더|오버|\+\d|-\d|무패|승/무|승무|스코어|경우|가능성|반면',s):
            d=direction(s,league,teams)
            if d: emit(context,md,'moneyline',d[0],d[1],s,method='explicit_final_prediction_section')
        # Restrict picks to explicit labels, not historical performance prose.
        m=re.search(r'(?:승무패\s*예측|승패\s*예측|승부\s*예상|승패|최종\s*예측|최종\s*추천|추천\s*픽|메인\s*픽|90분\s*방향|예측|추천\s*관점)\s*[:：]\s*(.{1,180})',s)
        if m:
            value=m[1];d=direction(value,league,teams)
            if d and not re.search(r'핸디|언더|오버|\+\d|-\d|무패|승/무|승무',value[:60]):
                emit(context,md,'moneyline',d[0],d[1],m[0])
            elif re.search(r'혼전|보류|관망|패스|우세|승',value):
                excluded.append(dict(source_url=doc['source_url'],author=doc['author'],match_date=md,league=league,teams=teams,reason='ambiguous_or_pass_prediction',evidence_excerpt=m[0][:160]))
        # Explicit totals with stated line; missing lines remain exclusions.
        m=re.search(r'(?:언더/오버|언더오버|오버/언더|총점)\s*(?:예측)?\s*[:：]\s*(언더|오버)\s*(\d+(?:\.\d+)?)',s)
        if m: emit(context,md,'total','under' if m[1]=='언더' else 'over',None,m[0],float(m[2]))
    # Deduplicate repetitions within one article, preserve different authors/articles.
    seen=set();unique=[]
    for r in rows:
        k=(r['match_date'],r['league'],tuple(r['teams']),r['market'],r['side'],r['line'])
        if k not in seen: seen.add(k);unique.append(r)
    return unique,excluded


def build():
    rows=[];excluded=[];odds_observations=[]
    for doc in read('documents.json',[]):
        r,e=parse_doc(doc);rows+=r;excluded+=e
        if not r and not e: excluded.append(dict(source_url=doc['source_url'],author=doc['author'],match_date=doc['match_date_hint'],reason='no_supported_explicit_pick_parsed'))
        odds_headers=[]
        for unit in doc['units']:
            if unit['tag']!='tr': odds_headers=[]
            if unit['tag']=='tr' and any('배당' in cell for cell in unit['cells']): odds_headers=unit['cells']
            fixture=matchup(unit['text'])
            if unit['tag']=='tr' and fixture and odds_headers and re.search(r'\d+\.\d+',unit['text']):
                odds_observations.append(dict(source_url=doc['source_url'],author=doc['author'],match_date=doc['match_date_hint'],league=fixture[0],teams=fixture[1],published_at_claim=doc['published_at_claim'],observed_at=doc['observed_at'],column_labels=odds_headers,quoted_cells=unit['cells'],price_time_quality='source_claim_only; no_historical_capture_timestamp',selection_mapping_verified=False))
    (OUT/'odds_observations.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in odds_observations),encoding='utf-8')
    quotes=defaultdict(list)
    for q in odds_observations: quotes[(q['source_url'],q['match_date'],q['league'],tuple(sorted(q['teams'])))].append(dict(column_labels=q['column_labels'],quoted_cells=q['quoted_cells'],selection_mapping_verified=False))
    directions=defaultdict(set)
    for r in rows:
        k=(r['source_url'],r['match_date'],r['league'],tuple(sorted(r['teams'])),r['market'],r['line'])
        directions[k].add(r['side'])
    for r in rows:
        r['source_odds_quotes']=quotes[(r['source_url'],r['match_date'],r['league'],tuple(sorted(r['teams'])))]
        k=(r['source_url'],r['match_date'],r['league'],tuple(sorted(r['teams'])),r['market'],r['line'])
        r['within_article_conflicting_directions']=len(directions[k])>1
    results=defaultdict(list)
    for result in read('official_results.json',[])+read('official_kbo_results.json',[]):
        results[(result['league'],result['match_date'],tuple(sorted([result['home'],result['away']])))].append(result)
    for r in rows:
        matches=results[(r['league'],r['match_date'],tuple(sorted(r['teams'])))]
        r['official_result_matches_count']=len(matches)
        r['result_join_status']='matched_unique_official_fixture' if len(matches)==1 else 'ambiguous_multiple_official_games' if len(matches)>1 else 'no_official_fixture_matched'
        r['settlement_rules_verified']=False
        if len(matches)==1:
            result=matches[0]
            r.update(source_side=r['side'],source_home=r['home'],source_away=r['away'],home=result['home'],away=result['away'],home_score=result['home_score'],away_score=result['away_score'],
                     score=dict(home=result['home_score'],away=result['away_score']),official_url=result['source_url'],official_id=result['official_id'],result_is_official=True)
            if r['selected_team']: r['side']='home' if r['selected_team']==r['home'] else 'away'
            r['teams_order_semantics']='teams_preserve_source_order; home_away_officially_joined'
    for name,data in [('candidates.jsonl',rows),('excluded_picks.jsonl',excluded)]:
        (OUT/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data),encoding='utf-8')
    fixture=lambda r:(r['league'],r['match_date'],tuple(sorted(r['teams'])))
    months={}
    for month in ['2025-10','2025-11','2025-12']+[f'2026-{m:02}' for m in range(1,11)]:
        rr=[r for r in rows if r['match_date'].startswith(month)]
        months[month]=dict(picks=len(rr),unique_fixtures=len({fixture(r) for r in rr}),authors=sorted({(r['source']+' / '+(r['author'] or 'unknown')) for r in rr}),by_league=dict(Counter(r['league'] for r in rr)))
    coverage=dict(target_start=START,target_end=END,discovered_articles=len(read('discovered.json',[])),captured_documents=len(read('documents.json',[])),pick_count=len(rows),unique_fixtures=len({fixture(r) for r in rows}),
                  distinct_match_dates=len({r['match_date'] for r in rows}),min_match_date=min((r['match_date'] for r in rows),default=None),max_match_date=max((r['match_date'] for r in rows),default=None),
                  by_source=dict(Counter(r['source'] for r in rows)),by_author=dict(Counter(r['source']+' / '+(r['author'] or 'unknown') for r in rows)),by_league=dict(Counter(r['league'] for r in rows)),
                  monthly=months,excluded_records=len(excluded),exclusions_by_reason=dict(Counter(r.get('reason') or r.get('exclusion_reason') for r in excluded)),original_odds_rows=sum(r['original_odds'] is not None for r in rows),unassigned_odds_quote_rows=len(odds_observations),rows_with_source_odds_quotes=sum(bool(r['source_odds_quotes']) for r in rows),conflicting_direction_rows=sum(r['within_article_conflicting_directions'] for r in rows),official_result_join_rows=sum(r['official_url'] is not None for r in rows),official_joins_by_league=dict(Counter(r['league'] for r in rows if r['official_url'])),cutoff_proven_rows=0,
                  limitations=['Public source publication dates are claims, first observed after matches.','A source may be AI generated; author identity is only displayed byline.','Same-fixture multiauthor picks and repeated markets are not independent games.','No ROI or official scores inferred from provider claims.','Source order home/away remains unverified pending official joins.','No picks in uncovered months must not be interpreted as no published picks.'])
    save('coverage.json',coverage)
    summary=f'''# 한국어 무료 팁스터 과거 자료 수집

목표 기간: {START}~{END}. 실제 픽: {coverage['min_match_date']}~{coverage['max_match_date']}.

현재 {len(rows)}픽, 고유 경기 {coverage['unique_fixtures']}개, 경기일 {coverage['distinct_match_dates']}일. 고유 경기는 리그·경기일·정규화한 양 팀으로 구분하며 같은 경기의 여러 작성자/시장 픽을 독립 경기로 세지 않습니다. 같은 날짜의 동일 팀 더블헤더는 경기 ID를 별도로 검증하지 못하면 구분하지 않습니다.

## 월별 실제 픽

'''+''.join(f"- {m}: {v['picks']}픽 / {v['unique_fixtures']}경기\n" for m,v in months.items())+f'''
## 수집 방법과 파일

- `discovered.json`: 결과를 보지 않고 공개 sitemap/카테고리에서 발견한 전체 후보 목록. {coverage['discovered_articles']}글.
- `documents.json`: {coverage['captured_documents']}개 글의 게시일 주장·표시 작성자·원문 SHA256·관측 시각과 짧은 구조/추천 근거. 전체 본문을 저장하지 않습니다.
- `candidates.jsonl`: 명시적 추천 필드·최종 예측 구역·예측 표를 보수적으로 파싱한 원본 픽. 공식 점수가 없으면 score/official_url은 null입니다.
- `excluded_picks.jsonl`, `listing_exclusions.json`, `excluded_documents.jsonl`: 기간 밖, 사후 게시 주장, 관망/모호한 픽, 지원하지 않는 시장, 전송/본문 식별 실패. 제외 기록 수는 서로 다른 원문 픽의 수와 같지 않습니다.
- `odds_observations.jsonl`: 원문에 있던 배당 표의 필드/수치를 보존합니다. 추천 방향과 배당의 대응을 검증하지 못하면 original_odds를 임의로 채우지 않습니다. 현재 {len(odds_observations)}개 표 행, {coverage['rows_with_source_odds_quotes']}픽에 관련 원문 배당 인용을 연결했습니다.
- `official_results.json`: NPB 공식 일별 최종 결과. 해외 수집본에서 읽은 NPB 날짜도 `overseas_npb_dates.json`에 보존합니다.
- `official_kbo_results.json`: KBO 공식 공개 월별 조회 결과. KBO 응답은 원정/홈 순서이므로 점수와 팀을 뒤집어 정상화했습니다. 취소 경기는 최종 점수로 저장하지 않습니다.
- `transport.jsonl`: 단일 순차 요청, 요청 사이 최소 0.55초, 재시도/로그인/차단 우회 없음. 공식 결과 수집은 다른 도메인의 독립 프로세스로 진행했습니다.
- `parser_tests.json`: 오프라인 회귀 검사. KBO 공식 홈/원정 예제, 과거 전적 표 오인 방지, 사후 게시 주장 제외 등을 확인합니다.

카테고리 전체 페이지를 순서대로 발견했습니다. 최초에는 전체 날짜순으로 수집했고, 중단 시에도 월별 범위를 확인할 수 있도록 이후에는 달을 순환하며 각 달 안에서 날짜순으로 모든 후보를 읽었습니다. 적중 여부를 수집/제외 기준으로 쓰지 않습니다. 현재 공식 결과 연결 {coverage['official_result_join_rows']}픽. 같은 글 안의 상충 방향 {coverage['conflicting_direction_rows']}픽은 표시해 두었고 합산 검증에서는 독립 표본으로 취급하면 안 됩니다.

## 범위와 한계

해피토토·픽스티비·베픽의 Python 요청은 연결이 끊겼습니다. sportsanalysis1의 최신 게시물은 2025년 7월이어서 목표 기간 밖입니다. 대체 공개 아카이브는 golfmasterj.tistory.com, guidesyj.tistory.com, sportsanalyst1.tistory.com입니다. 표시 작성자명은 사람의 신원이나 서로 다른 사람임을 검증한 것이 아닙니다. AI 작성 가능성도 있습니다. KBL은 이미 받은 혼합 종목 글의 명시적 추천만 포함했습니다.

목표 기간의 모든 달을 확보한 것은 아닙니다. 빈 달은 미확보이며, 게시물이 없었다는 뜻이 아닙니다. 게시 시각은 사이트의 주장이고 최초 관측은 경기 후입니다. 경기 전 원문 불변성/구매 가능 배당 시각은 입증하지 못했습니다. provider 자체 점수는 공식 결과로 쓰지 않았습니다. K리그/KBL 공식 결과는 병행 작업에서 연결하므로 이 수집기에서 네트워크 조회하지 않습니다. 본 산출물은 통계 검증·ROI 입증·운영 채택·배포가 아닙니다.

## 재현

```powershell
python scripts/research_collect_year_korean.py --build --self-test
```

추가 네트워크 수집은 `--discover --prepare-inventory --capture`, 공식 결과는 `--official-npb --official-kbo`로 실행합니다. 기존 산출물의 오프라인 재생과 새 네트워크 관측은 구분해야 합니다.
'''
    (OUT/'README.md').write_text(summary,encoding='utf-8')
    print(json.dumps({k:v for k,v in coverage.items() if k not in ['monthly','limitations']},ensure_ascii=False),flush=True)
    return rows,excluded


def official_npb():
    jp={'g':'요미우리 자이언츠','s':'야쿠르트 스왈로즈','db':'요코하마 DeNA 베이스타스','d':'주니치 드래곤즈','t':'한신 타이거스','c':'히로시마 도요 카프',
        'f':'니혼햄 파이터스','e':'라쿠텐 골든이글스','l':'세이부 라이온즈','m':'지바 롯데 마린스','b':'오릭스 버팔로스','h':'소프트뱅크 호크스'}
    dates=set()
    for row in read('discovered.json',[]):
        title=row['title_hint'];fixture=matchup(title)
        if not ('NPB' in title or '일야' in title or fixture and fixture[0]=='NPB'): continue
        md=get_date(title) or (row.get('sitemap_lastmod_claim') or '')[:10]
        if md and START<=md<=END: dates.add(md)
    results={r['official_id']:r for r in read('official_results.json',[])}
    dates.update(read('overseas_npb_dates.json',[]))
    checked=set(read('official_dates_checked.json',[]))
    for md in sorted(dates):
        if md in checked: continue
        u='https://npb.jp/bis/'+md[:4]+'/games/gm'+md.replace('-','')+'.html'
        data,meta=fetch(u,'official_npb_daily_final_results')
        if data:
            soup=BeautifulSoup(data,'html.parser')
            for a in soup.select('a.link_box[href]'):
                m=re.search(r'/scores/\d{4}/\d{4}/([a-z]+)-([a-z]+)-',a['href'])
                scores=[x.get_text(strip=True) for x in a.select('.score_text')]
                if not m or len(scores)!=2 or not all(x.isdigit() for x in scores) or m[1] not in jp or m[2] not in jp:
                    append('official_result_exclusions.jsonl',dict(match_date=md,source_url=u,reason='not_numeric_final_or_cancelled',evidence_excerpt=norm(a.get_text(' ',strip=True))[:150]));continue
                oid=a['href'];results[oid]=dict(league='NPB',match_date=md,home=jp[m[1]],away=jp[m[2]],home_score=int(scores[0]),away_score=int(scores[1]),source_url=u,official_url=urljoin(u,oid),official_id=oid,is_official_result_source=True,observed_at=meta['observed_at'],source_sha256=meta['sha256'])
            checked.add(md);save('official_dates_checked.json',sorted(checked));save('official_results.json',list(results.values()))
        if len(checked)%10==0: print('official NPB dates',len(checked),'results',len(results),flush=True)
    print('official NPB complete',len(checked),'dates',len(results),'finals',flush=True)


def official_kbo():
    results={r['official_id']:r for r in read('official_kbo_results.json',[])}
    requests_to_make=[('2026',f'{m:02}','0,9,6') for m in range(3,11)]+[('2025','10','3,4,5,7'),('2026','10','3,4,5,7')]
    url='https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList'
    for year,month,series in requests_to_make:
        params=dict(leId='1',srIdList=series,seasonId=year,gameMonth=month,teamId='')
        time.sleep(.55)
        try:
            r=requests.post(url,data=params,headers={'Referer':'https://www.koreabaseball.com/Schedule/Schedule.aspx','X-Requested-With':'XMLHttpRequest'},timeout=15)
            meta=dict(url=url,method='POST',purpose='official_kbo_monthly_results',request_parameters=params,status=r.status_code,final_url=r.url,observed_at=now(),response_bytes=len(r.content),sha256=hashlib.sha256(r.content).hexdigest())
            append('transport.jsonl',meta)
            try: data=r.json()
            except ValueError:
                append('official_result_exclusions.jsonl',dict(**meta,reason='kbo_non_json_response'));continue
            for row in data.get('rows',[]):
                cells=row['row'];play=next((x['Text'] for x in cells if x.get('Class')=='play'),None)
                links=' '.join(x.get('Text') or '' for x in cells)
                gid=re.search(r'gameId=([0-9A-Z]+)',links)
                if not play or not gid: continue
                gameid=gid[1];md=f'{gameid[:4]}-{gameid[4:6]}-{gameid[6:8]}'
                if not START<=md<=END: continue
                soup=BeautifulSoup(play,'html.parser');names=soup.find_all('span',recursive=False);scores=soup.select('em span')
                if len(names)!=2 or len(scores)!=3 or not scores[0].get_text().isdigit() or not scores[2].get_text().isdigit():
                    append('official_result_exclusions.jsonl',dict(match_date=md,source_url=url,official_id=gameid,reason='not_numeric_final_or_cancelled',evidence_excerpt=soup.get_text(' ',strip=True)[:100]));continue
                away=teams_in(names[0].get_text(),'KBO');home=teams_in(names[1].get_text(),'KBO')
                if len(away)!=1 or len(home)!=1: continue
                review=f'https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?gameDate={gameid[:8]}&gameId={gameid}&section=REVIEW'
                results[gameid]=dict(league='KBO',match_date=md,home=home[0],away=away[0],home_score=int(scores[2].get_text()),away_score=int(scores[0].get_text()),source_url=review,official_id=gameid,is_official_result_source=True,observed_at=meta['observed_at'],source_sha256=meta['sha256'],retrieval_url=url,retrieval_parameters=params)
            save('official_kbo_results.json',list(results.values()));print('KBO official',year,month,series,'cumulative',len(results),flush=True)
        except requests.RequestException as e:
            append('transport.jsonl',dict(url=url,method='POST',purpose='official_kbo_monthly_results',request_parameters=params,observed_at=now(),error=type(e).__name__,detail=str(e)[:180]))


def self_test():
    assert matchup('닛폰햄 파이터스 vs 지바 롯데 마린스')==('NPB',['니혼햄 파이터스','지바 롯데 마린스'])
    assert matchup('LG 트윈스 vs 삼성 라이온즈')==('KBO',['LG 트윈스','삼성 라이온즈'])
    assert direction('원정팀 승','KBO',['LG 트윈스','삼성 라이온즈'])==('away','삼성 라이온즈')
    assert direction('혼전','KBO',['LG 트윈스','삼성 라이온즈']) is None
    assert direction('삼성 승 추천하지 않음','KBO',['LG 트윈스','삼성 라이온즈']) is None
    assert get_date('2026년 4월 1일')=='2026-04-01'
    docs=read('documents.json',[]);rows,excluded=build()
    assert rows
    for r in rows:
        assert START<=r['match_date']<=END
        assert r['published_at_claim'] is None or r['published_at_claim'][:10]<=r['match_date']
        assert (r['official_url'] is None and r['score'] is None or r.get('result_is_official') is True) and r['cutoff_proven'] is False
        assert r['selected_team'] in r['teams'] or r['side'] in ['draw','under','over']
        assert len(r['evidence_excerpt'])<=160
    # Source's historical result tables must never create picks.
    base=dict(source='example.org',source_url='https://example.org/a',author=None,published_at_claim='2026-04-01T01:00:00+09:00',observed_at=now(),sha256='test',title='2026년 4월 1일 LG vs 삼성',match_date_hint='2026-04-01')
    hist=dict(base,units=[dict(tag='tr',text='날짜 | 경기 | 결과',cells=['날짜','경기','결과']),dict(tag='tr',text='2026-03-31 | LG vs 삼성 | 삼성 승',cells=['2026-03-31','LG vs 삼성','삼성 승'])])
    assert parse_doc(hist)[0]==[]
    late=dict(base,published_at_claim='2026-04-02T01:00:00+09:00',units=[dict(tag='p',text='승패: 삼성 승')])
    assert parse_doc(late)[0]==[] and parse_doc(late)[1][0]['exclusion_reason']=='claimed_publication_after_match_date'
    assert direction('승','KBO',['LG 트윈스','삼성 라이온즈'])==('home','LG 트윈스')
    assert matchup('요코하마 마리노스 vs 산프레체 히로시마') is None
    kbo={r['official_id']:r for r in read('official_kbo_results.json',[])}
    if '20260401HTLG0' in kbo:
        golden=kbo['20260401HTLG0']
        assert (golden['away'],golden['away_score'],golden['home'],golden['home_score'])==('KIA 타이거즈',2,'LG 트윈스',7)
    for r in rows:
        if r['official_result_matches_count']!=1: assert r['official_url'] is None
        if r['official_url']:
            assert r['official_result_matches_count']==1
            assert isinstance(r['home_score'],int) and isinstance(r['away_score'],int)
            assert urlparse(r['official_url']).netloc in {'npb.jp','www.koreabaseball.com'}
        assert r['settlement_rules_verified'] is False and 'outcome' not in r
    save('parser_tests.json',dict(result='passed',tested_at=now(),documents=len(docs),candidate_rows=len(rows),checks=['longest_alias','away_direction','pass_exclusion','negative_recommendation_exclusion','date_parse','schema','no_provider_score_as_official','historical_table_not_pick','late_publication_excluded'],network_requests=0))
    print('parser tests passed',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ['discover','prepare-inventory','capture','official-npb','official-kbo','build','self-test']: p.add_argument('--'+arg,action='store_true')
    args=p.parse_args()
    if args.discover: discover()
    if args.prepare_inventory: prepare_inventory()
    if args.capture: capture()
    if args.official_npb: official_npb()
    if args.official_kbo: official_kbo()
    if args.build: build()
    if args.self_test: self_test()
