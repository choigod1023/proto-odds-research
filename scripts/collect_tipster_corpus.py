"""Bounded public archive retrieval. Raw article cache stays local, not in Git."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup

BASE='https://www.wstv24.com'

def get(url, cache):
    path=cache/(hashlib.sha256(url.encode()).hexdigest()+'.html')
    if path.exists(): return path.read_text(encoding='utf8')
    time.sleep(.5)
    r=requests.get(url,timeout=25)
    r.raise_for_status()
    if len(r.content)<1000: raise ValueError('empty response')
    r.encoding=r.apparent_encoding
    path.write_text(r.text,encoding='utf8')
    return r.text

def listing(task, cache):
    category,page=task
    params={'page':page,'sca':category}
    if category.startswith('search:'):
        params={'page':page,'sfl':'wr_subject','stx':category[7:],'sop':'and'}
    url=BASE+'/analysis?'+urlencode(params)
    soup=BeautifulSoup(get(url,cache),'html.parser')
    rows=[]
    for a in soup.select('a[href]'):
        title=a.get_text(' ',strip=True)
        match=re.search(r'/analysis/(\d+)',a['href'])
        if match and re.search(r'202[345]년',title):
            rows.append(dict(url=BASE+'/analysis/'+match[1],title=title,category=category,listing=url))
    return rows

def article(row,cache):
    soup=BeautifulSoup(get(row['url'],cache),'html.parser')
    body=soup.select_one('#board_view_con')
    article=soup.find('article')
    if not body or not article: raise ValueError('full article missing')
    text=body.get_text(' ',strip=True)
    p=re.search(r'승무패\s*[:：]\s*(.*?)(?=핸디캡|언더오버|$)',text)
    info=article.get_text(' ',strip=True)
    published=re.search(r'202\d-\d\d-\d\d \d\d:\d\d',info)
    author=article.select_one('.view-nick a')
    cached_path=cache/(hashlib.sha256(row['url'].encode()).hexdigest()+'.html')
    return dict(**row,body=text,body_sha256=hashlib.sha256(text.encode()).hexdigest(),
                pick=p.group(1).strip() if p else None,
                displayed_published=published.group() if published else None,
                author=author.get_text(' ',strip=True) if author else None,
                observed_at=datetime.fromtimestamp(cached_path.stat().st_mtime,timezone.utc).isoformat(),
                full_article=True)

def run(out, pages, workers, categories=None):
    out.mkdir(parents=True,exist_ok=True); cache=out/'cache';cache.mkdir(exist_ok=True)
    found={}; failures=[]
    tasks=[(cat,p) for cat in (categories or ('배구','NBA','MLB','해외축구','농구','야구','축구')) for p in range(1,pages+1)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fs={pool.submit(listing,t,cache):t for t in tasks}
        for i,f in enumerate(as_completed(fs),1):
            try:
                for r in f.result(): found[r['url']]=r
            except Exception as e: failures.append({'listing':fs[f],'error':str(e)[:150]})
            if i%10==0: print('listing',i,'/',len(tasks),'articles',len(found),flush=True)
    (out/'discovered.json').write_text(json.dumps(list(found.values()),ensure_ascii=False,indent=2),encoding='utf8')
    rows=[]
    # Stable URL order avoids ordering by observed results or article success.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fs={pool.submit(article,r,cache):r for r in sorted(found.values(),key=lambda x:x['url'])}
        for i,f in enumerate(as_completed(fs),1):
            try: rows.append(f.result())
            except Exception as e: failures.append({'url':fs[f]['url'],'error':str(e)[:150]})
            if i%25==0:
                (out/'articles.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
                print('full_articles',i,'/',len(fs),'ok',len(rows),'failures',len(failures),flush=True)
    (out/'articles.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
    (out/'failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf8')
    print('complete',len(rows),'articles',flush=True)


def reparse_cached(out):
    """Refresh parser metadata without sending any HTTP requests."""
    cache=out/'cache'
    found=json.loads((out/'discovered.json').read_text(encoding='utf8'))
    rows=[]
    old_path=out/'articles.json'
    old={r['url']:r for r in json.loads(old_path.read_text(encoding='utf8'))} if old_path.exists() else {}
    for row in found:
        cached=cache/(hashlib.sha256(row['url'].encode()).hexdigest()+'.html')
        if cached.exists():
            if row['url'] in old:
                previous=old[row['url']]
                html=cached.read_text(encoding='utf8')
                nick=re.search(r'<span class="view-nick">(.*?)</div>',html,re.S)
                author=BeautifulSoup(nick.group(1),'html.parser').find('a') if nick else None
                previous['author']=author.get_text(' ',strip=True) if author else None
                previous['observed_at']=datetime.fromtimestamp(cached.stat().st_mtime,timezone.utc).isoformat()
                rows.append(previous)
            else:
                rows.append(article(row,cache))
    (out/'articles.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
    print('offline reparsed',len(rows),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--pages',type=int,default=18);ap.add_argument('--workers',type=int,default=3)
    ap.add_argument('--categories',nargs='+')
    ap.add_argument('--cached-only',action='store_true')
    a=ap.parse_args()
    if a.cached_only: reparse_cached(a.out)
    else: run(a.out,a.pages,a.workers,a.categories)
