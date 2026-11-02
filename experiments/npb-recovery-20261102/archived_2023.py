from bs4 import BeautifulSoup
import json, re, hashlib
from pathlib import Path
ALIASES={'北海道日本ハム':('닛폰햄','北海道日本ハムファイターズ'),'東北楽天':('라쿠텐','東北楽天ゴールデンイーグルス'),'読売':('요미우리','読売ジャイアンツ'),'中日':('주니치','中日ドラゴンズ'),'東京ヤクルト':('야쿠르트','東京ヤクルトスワローズ'),'広島東洋':('히로시마','広島東洋カープ')}
def parse(raw, meta):
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']: raise ValueError('hash mismatch')
    s=BeautifulSoup(raw,'html.parser'); title=s.title.get_text()
    m=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日.*（(.+?)vs(.+?)）',title)
    if not m: return None
    year,month,day,home,away=m.groups()
    if year!='2023' or home not in ALIASES or away not in ALIASES: return None
    summary=s.select_one('table.gmcolorsub'); text=summary.get_text(' ',strip=True) if summary else ''; scores={}
    for short in (home,away):
        found=re.search(re.escape(ALIASES[short][1])+r'\s+(\d+)',text)
        if not found: return None
        scores[short]=int(found.group(1))
    tm=re.search(r'開始\s*(\d{1,2}):(\d{2})',s.get_text(' ',strip=True))
    pitchers=[t for t in s.select('table.gmtbltop') if t.get_text(' ',strip=True).startswith('投 回')]
    if not tm or len(pitchers)!=2: return None
    names=[]
    for t in pitchers:
        rows=t.find_all('tr'); cells=rows[1].find_all(['th','td'],recursive=False) if len(rows)>1 else []
        if len(cells)<3 or not cells[1].get_text(strip=True): return None
        names.append(cells[1].get_text(strip=True))
    return {'date':f'{year}-{int(month):02d}-{int(day):02d}','home':ALIASES[home][0],'away':ALIASES[away][0], 'home_starter_name':names[1],'away_starter_name':names[0], 'home_score':scores[home],'away_score':scores[away], 'official_start_hhmm':f'{int(tm[1]):02d}:{tm[2]}','source_url':meta['url'],'source_sha256':meta['sha256'],'source_observed_at':meta['observed_at'], 'official_game_page_url':meta['url'],'official_player_id':None}
def daily_pairs(raw):
    soup=BeautifulSoup(raw,'html.parser'); out=[]
    for a in soup.find_all('a',href=True):
        if not re.fullmatch(r's\d+\.html',a['href']): continue
        tr=a.find_parent('tr'); prev=tr.find_previous_sibling('tr') if tr else None
        imgs=prev.find_all('img',src=True) if prev else []
        codes=[m.group(1) for img in imgs if (m:=re.search(r'pet2023_([a-z]+)_',img['src']))]
        if len(codes)==2: out.append((a['href'],tuple(codes)))
    return out

def load():
    out=[]
    for p in sorted(Path('experiments/npb-recovery-20261102/raw').glob('*.json')):
        meta=json.loads(p.read_text(encoding='utf8')); r=parse(p.with_suffix('.html').read_bytes(),meta)
        if r: out.append(r)
    return out
