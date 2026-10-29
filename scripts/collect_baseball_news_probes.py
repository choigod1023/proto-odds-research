"""Small manually reviewed announcement-source probes, NOT a representative sample."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import hashlib
import json
import re

import requests
from bs4 import BeautifulSoup

from audit_baseball_announcements import eligible_news

SOURCES=[
    dict(league='KBO',date='2026-03-29',home='LG',away='KT',side='home',start='2026-03-29T14:00:00+09:00',
         starter='임찬규',lineup='홍창기 신민재 오스틴 문보경 박동원 문성주 오지환 구본혁 박해민'.split(),
         url='https://biz.chosun.com/sports/baseball/2026/03/29/GA2TKOJQG4YGKYZQMZTGENRTG4/?outputType=amp'),
    dict(league='KBO',date='2026-04-02',home='LG',away='KIA',side='home',start='2026-04-02T18:30:00+09:00',
         starter='웰스',lineup=['홍창기','신민재','오스틴 딘','문보경','박동원','문성주','천성호','구본혁','박해민'],
         url='https://www.mk.co.kr/news/sports/12006262'),
    dict(league='KBO',date='2026-04-03',home='키움',away='LG',side='away',start='2026-04-03T18:30:00+09:00',
         starter='치리노스',lineup='홍창기 신민재 오스틴 문보경 박동원 문성주 천성호 구본혁 박해민'.split(),
         url='https://v.daum.net/v/20260403173442083'),
    dict(league='KBO',date='2026-08-01',home='KT',away='한화',side='away',start='2026-08-01T18:00:00+09:00',
         starter='짐머맨',lineup='이원석 페라자 문현빈 강백호 노시환 채은성 허인서 이도윤 심우준'.split(),
         url='https://www.mt.co.kr/sports/2026/08/01/202608011620773072O'),
    dict(league='NPB',date='2026-08-01',home='야쿠르트',away='한신',side='away',start='2026-08-01T18:00:00+09:00',
         starter='伊藤将',lineup='近本 中野 森下 佐藤 大山 立石 坂本 元山 伊藤将'.split(),
         url='https://www.nikkansports.com/baseball/news/202608010000785.html?mode=all')]


def publication(soup):
    published=modified=None
    for meta in soup.find_all('meta'):
        k=meta.get('property') or meta.get('name') or '';v=meta.get('content')
        if k=='article:published_time' and v:published=v
        if k=='article:modified_time' and v:modified=v
    for script in soup.find_all('script',type='application/ld+json'):
        try:
            obj=json.loads(script.string or script.get_text());items=obj if isinstance(obj,list) else [obj]
            for item in items:
                if item.get('datePublished'):published=item['datePublished']
                if item.get('dateModified'):modified=item['dateModified']
        except (ValueError,TypeError,AttributeError):pass
    if not published:
        text=soup.get_text(' ',strip=True)
        m=re.search(r'\[(\d{4})年(\d{1,2})月(\d{1,2})日(\d{1,2})時(\d{1,2})分\]',text)
        if m:published=f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}T{int(m[4]):02d}:{int(m[5]):02d}:00+09:00'
    return published,modified


def collect(folder):
    out=[]
    for source in SOURCES:
        try:
            r=requests.get(source['url'],timeout=25);r.raise_for_status();soup=BeautifulSoup(r.content,'lxml')
            text=re.sub(r'\s+','',soup.get_text(' ',strip=True))
            if any(re.sub(r'\s+','',n) not in text for n in source['lineup']+[source['starter']]):raise ValueError('source_does_not_verify_reviewed_player_names')
            published,modified=publication(soup)
            if not published:raise ValueError('missing_exact_publication_time')
            ok,reason=eligible_news(published,modified,source['start'],source['lineup'])
            out.append(dict(source,published_at=published,modified_at=modified,accepted=ok,reason=reason,
                collected_at=datetime.now(timezone.utc).isoformat(),response_sha256=hashlib.sha256(r.content).hexdigest(),
                source_kind='dated_original_reporting_retrieved_later',complete_both_teams=False,
                immutable_pregame_capture=False))
        except Exception as e:out.append(dict(source,accepted=False,reason=str(e)[:150]))
    (folder/'news-announcement-probes.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in out),encoding='utf8',newline='\n')
    print(json.dumps([dict(league=r['league'],date=r['date'],side=r['side'],accepted=r['accepted'],reason=r['reason']) for r in out],ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);collect(p.parse_args().folder)
