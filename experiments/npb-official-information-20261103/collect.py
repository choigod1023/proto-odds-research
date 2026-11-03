"""Bounded NPB official source capture; no model inputs or fitting."""
from __future__ import annotations
import hashlib, json, gzip, os, re, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'raw'
RAW.mkdir(exist_ok=True)
LOG, LOCK = ROOT / 'attempts.jsonl', ROOT / '.collector.lock'
LIMIT, INTERVAL, TIMEOUT = 500, 1.0, 15
TEAMS = {'読売ジャイアンツ':'g','阪神タイガース':'t','横浜DeNAベイスターズ':'db','中日ドラゴンズ':'d','広島東洋カープ':'c','東京ヤクルトスワローズ':'s','福岡ソフトバンクホークス':'h','北海道日本ハムファイターズ':'f','オリックス・バファローズ':'b','東北楽天ゴールデンイーグルス':'e','埼玉西武ライオンズ':'l','千葉ロッテマリーンズ':'m'}
RANGES = [('2026-03-27','2026-03-31'),('2026-04-14','2026-04-20'),('2026-05-12','2026-05-18'),('2026-06-16','2026-06-22'),('2026-07-14','2026-07-20'),('2026-08-04','2026-08-10')]

def sha(data): return hashlib.sha256(data).hexdigest()
def write_json(path, obj): path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf8', newline='\n')
def read_attempts(): return [json.loads(line) for line in LOG.read_text(encoding='utf8').splitlines()] if LOG.exists() else []
def log_event(obj):
    with LOG.open('a', encoding='utf8', newline='\n') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
def dates():
    for start, end in RANGES:
        d, last = date.fromisoformat(start), date.fromisoformat(end)
        while d <= last:
            yield d
            d += timedelta(days=1)
def body_paths(url):
    name = sha(url.encode()); return RAW / (name+'.body.gz'), RAW / (name+'.meta.json')
def load(url):
    bp, mp = body_paths(url)
    if not bp.exists() or not mp.exists(): return None
    meta = json.loads(mp.read_text(encoding='utf8')); body = gzip.decompress(bp.read_bytes())
    if meta['url'] != url or sha(body) != meta['body_sha256']: raise ValueError('cached source mismatch')
    return body, meta

class Capture:
    def __init__(self):
        entries = read_attempts(); self.started = sum(x['kind']=='request_started' for x in entries)
        self.last = 0; self.failures = 0; self.halted = None; self.session = requests.Session()
        self.session.headers.update({'User-Agent':'NPB official research archive capture'})
        if any(x.get('http_status') in (403,429) for x in entries): self.halted = 'previous_http_403_or_429'
        for item in reversed([x for x in entries if x['kind']=='request_completed']):
            if item.get('http_status') == 200: break
            self.failures += 1
        if self.failures >= 3: self.halted = self.halted or 'three_consecutive_failures'
    def get(self, url):
        cached = load(url)
        if cached: return cached
        if self.halted or self.started >= LIMIT:
            self.halted = self.halted or 'request_budget'; return None
        if urlparse(url).scheme != 'https' or urlparse(url).netloc != 'npb.jp': raise ValueError('unexpected host')
        wait = INTERVAL - (time.monotonic() - self.last)
        if wait > 0: time.sleep(wait)
        started = datetime.now(timezone.utc).isoformat(); self.started += 1
        log_event({'kind':'request_started','url':url,'method':'GET','params':{},'started_at':started,'timeout_seconds':TIMEOUT})
        response = None; error = None
        try: response = self.session.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as exc: error = type(exc).__name__
        self.last = time.monotonic(); received = datetime.now(timezone.utc).isoformat(); status = response.status_code if response is not None else None
        meta = {'url':url,'method':'GET','params':{},'started_at':started,'received_at':received,'http_status':status,'error':error,'content_type':response.headers.get('Content-Type') if response is not None else None,'availability':'retrospective_http_received_at','body_sha256':sha(response.content) if response is not None else None}
        if response is not None:
            bp, mp = body_paths(url); bp.write_bytes(gzip.compress(response.content, mtime=0)); write_json(mp, meta)
        log_event({'kind':'request_completed',**meta})
        if status != 200:
            self.failures += 1
            if status in (403,429): self.halted = f'http_{status}'
            elif self.failures >= 3: self.halted = 'three_consecutive_failures'
            return None
        self.failures = 0; return response.content, meta

def census(raw, day):
    soup = BeautifulSoup(raw, 'html.parser'); text = soup.get_text(' ', strip=True)
    if f'{day.year}年{day.month}月{day.day}日' not in text: raise ValueError('date missing in daily page')
    prefix = f'/scores/{day.year}/{day:%m%d}/'; links = []; observed=[]
    for a in soup.find_all('a', href=True):
        path = urlparse(a['href']).path
        if path.startswith(prefix): observed.append(path)
        if re.fullmatch(re.escape(prefix)+r'[a-z]+-[a-z]+-\d+/', path): links.append(path)
    if set(observed) != set(links): raise ValueError('unrecognized official score path')
    if not links and '該当する試合がありません' not in text: raise ValueError('empty day lacks explicit no-game confirmation')
    return sorted(set(links))

def innings_outs(text):
    s = re.sub(r'\s+','',text); m = re.fullmatch(r'(\d+)(?:\.([012])|\+)?',s)
    if not m: raise ValueError('invalid baseball innings')
    return 3*int(m[1])+int(m[2] or 0)
def get_cells(row): return row.find_all(['th','td'], recursive=False)
def txt(cell): return cell.get_text(' ', strip=True)
def profile(cell):
    a = cell.find('a', href=True)
    if not a: return None
    m = re.fullmatch(r'/bis/players/(\d+)\.html', urlparse(a['href']).path)
    if not m: return None
    return {'name':a.get_text(' ',strip=True),'official_player_id':m[1],'profile_url':'https://npb.jp'+urlparse(a['href']).path}

def batting(soup, side, team):
    t = soup.select_one(f'#tablefix_{side}_b'); heading = t.find_previous(['h3','h4','h5']) if t else None
    if not t or not heading or TEAMS.get(heading.get_text(' ',strip=True)) != team: raise ValueError('batting team mismatch')
    rows = t.find_all('tr'); headers = [txt(c) for c in get_cells(rows[0])]; starters=[]; subs=[]
    if len(headers)<3 or headers[1:3] != ['守備','選手']: raise ValueError('batting header mismatch')
    for row in rows[1:]:
        c=get_cells(row)
        if len(c)<3: continue
        order,pos=txt(c[0]),txt(c[1]); player=profile(c[2])
        if re.fullmatch('[1-9]',order):
            if not player or not re.match(r'^[（(][^）)]+[）)]',pos): raise ValueError('starter role or player ID missing')
            starters.append({'order':int(order),'position_display':pos,**player})
        elif not order and player: subs.append({'position_display':pos,**player})
    if sorted(x['order'] for x in starters)!=list(range(1,10)) or len({x['official_player_id'] for x in starters})!=9: raise ValueError('invalid starting nine')
    return sorted(starters,key=lambda x:x['order']),subs

def pitching(soup,side,team):
    t=soup.select_one(f'#tablefix_{side}_p'); heading=t.find_previous(['h3','h4','h5']) if t else None
    if not t or not heading or TEAMS.get(heading.get_text(' ',strip=True))!=team: raise ValueError('pitching team mismatch')
    rows=t.find_all('tr'); headers=[txt(c) for c in get_cells(rows[0])]
    if '投手' not in headers or '投球回' not in headers: raise ValueError('pitch header missing')
    pi,ii=headers.index('投手'),headers.index('投球回'); result=[]
    for row in rows[1:]:
        c=get_cells(row)
        if len(c)!=len(headers) or not txt(c[pi]): continue
        if 'チーム計' in txt(c[pi]): continue
        player=profile(c[pi])
        if not player: raise ValueError('pitcher profile absent')
        details={'role':'starter' if not result else 'reliever','innings_display':txt(c[ii]),'innings_outs':innings_outs(txt(c[ii])),**player}
        for col in ('投球数','打者'):
            if col in headers:
                v=txt(c[headers.index(col)]); details['pitch_count' if col=='投球数' else 'batters_faced']=int(v) if v.isdigit() else None
        result.append(details)
    if not result: raise ValueError('pitchers absent')
    return result

def final_score(soup,home,away):
    table=soup.select_one('#tablefix_ls')
    if not table: raise ValueError('score table missing')
    rows=[[txt(c) for c in get_cells(row)] for row in table.find_all('tr')]
    if len(rows)!=3 or '計' not in rows[0]: raise ValueError('score table structure invalid')
    i=rows[0].index('計'); names={code:name for name,code in TEAMS.items()}
    if names[away] not in rows[1][0] or names[home] not in rows[2][0]: raise ValueError('score team mismatch')
    if len(rows[1])<=i or len(rows[2])<=i or not rows[1][i].isdigit() or not rows[2][i].isdigit(): raise ValueError('final score missing')
    return {'home':int(rows[2][i]),'away':int(rows[1][i])}

def parse_game(raw,url,day):
    soup=BeautifulSoup(raw,'html.parser'); text=soup.get_text(' ',strip=True)
    m=re.fullmatch(r'/scores/2026/(\d{4})/([a-z]+)-([a-z]+)-(\d+)/box.html',urlparse(url).path)
    if not m or m[1]!=day.strftime('%m%d'): raise ValueError('fixture id date mismatch')
    dm=re.search(r'2026年\s*(\d+)月\s*(\d+)日',text)
    if not dm or (int(dm[1]),int(dm[2]))!=(day.month,day.day): raise ValueError('source date mismatch')
    base={'official_game_id':m[0].removesuffix('box.html'),'date':day.isoformat(),'home_team_id':m[2],'away_team_id':m[3],'source_url':url}
    called = 'コールドゲーム' in text and '◇終了' in text and '◇開始' in text
    if '試合終了' not in text and not called: return {**base,'state':'cancelled' if '中止' in text else 'nonfinal','postgame':None}
    sides={}
    for side,code in [('away',m[3]),('home',m[2])]:
        prefix='t' if side=='away' else 'b'; lineup,substitutes=batting(soup,prefix,code); pitchers=pitching(soup,prefix,code)
        sides[side]={'actual_starting_lineup':lineup,'batting_substitutions':substitutes,'actual_pitchers':pitchers}
    sides['official_final_score']=final_score(soup,m[2],m[3])
    start=re.search(r'◇開始\s*(\d{1,2}):(\d{2})',text)
    if not start: raise ValueError('actual start missing')
    return {**base,'state':'completed','completion_type':'called_game' if called else 'normal_or_extra_innings','actual_start_jst':f'{day.isoformat()}T{int(start[1]):02d}:{start[2]}:00+09:00','availability':'retrospective_postgame_only','strict_pit_available':False,'postgame':sides}

def run():
    cap=Capture(); census_rows=[]; by_month=defaultdict(list)
    for day in dates():
        url=f'https://npb.jp/bis/2026/games/gm{day:%Y%m%d}.html'; data=cap.get(url); row={'date':day.isoformat(),'source_url':url}
        if data is None: row.update({'status':'not_available','reason':cap.halted or 'request_failed'})
        elif data[1]['http_status']!=200: row.update({'status':'request_failed','http_status':data[1]['http_status']})
        else:
            try:
                links=census(data[0],day); row.update({'status':'ok','source_sha256':data[1]['body_sha256'],'received_at':data[1]['received_at'],'fixture_paths':links}); by_month[day.strftime('%Y-%m')].extend((day,x) for x in links)
            except ValueError as exc: row.update({'status':'parse_failed','reason':str(exc)})
        census_rows.append(row)
        print('census',day,row['status'],'requests',cap.started,flush=True)
        if cap.halted: break
    selected=[]
    for month in sorted({r['date'][:7] for r in census_rows}):
        month_rows=[r for r in census_rows if r['date'].startswith(month)]
        expected=sum(d.strftime('%Y-%m')==month for d in dates())
        if len(month_rows)!=expected or any(x['status']!='ok' for x in month_rows): continue
        for day,path in sorted(set(by_month[month]))[:20]: selected.append((day,path))
    events=[]
    for day,path in selected:
        url='https://npb.jp'+path+'box.html'; data=cap.get(url); row={'date':day.isoformat(),'official_game_id':path,'source_url':url}
        if data is None: row.update({'state':'not_available','reason':cap.halted or 'request_failed'})
        elif data[1]['http_status']!=200: row.update({'state':'request_failed','http_status':data[1]['http_status']})
        else:
            try:
                row.update(parse_game(data[0],url,day)); row.update({'source_sha256':data[1]['body_sha256'],'received_at':data[1]['received_at']})
            except ValueError as exc: row.update({'state':'parse_failed','reason':str(exc),'source_sha256':data[1]['body_sha256'],'received_at':data[1]['received_at']})
        events.append(row); print('games',len(events),'/',len(selected),row['state'],'requests',cap.started,flush=True)
        if cap.halted: break
    for day,path in selected[len(events):]: events.append({'date':day.isoformat(),'official_game_id':path,'state':'not_attempted','reason':cap.halted})
    (ROOT/'fixture-census.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in census_rows),encoding='utf8',newline='\n')
    (ROOT/'postgame-records.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in events),encoding='utf8',newline='\n')
    counts=Counter(x['state'] for x in events); completed=[x for x in events if x['state']=='completed']
    summary={'scope':'2026 official selected windows; postgame retrospective','census_days_planned':sum(1 for _ in dates()),'census_days_attempted':len(census_rows),'census_day_status':dict(Counter(x['status'] for x in census_rows)),'census_fixture_paths':sum(len(x.get('fixture_paths',[])) for x in census_rows),'selected_fixtures':len(selected),'game_status':dict(counts),'completed':len(completed),'nine_per_side':sum(all(len(x['postgame'][s]['actual_starting_lineup'])==9 for s in ('home','away')) for x in completed),'pitchers_both_sides':sum(all(x['postgame'][s]['actual_pitchers'] for s in ('home','away')) for x in completed),'strict_pit_verified':0,'requests_started':cap.started,'new_request_limit':LIMIT,'halted':cap.halted}
    write_json(ROOT/'manifest.json',summary); print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try: fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: sys.exit('collector lock exists: another run may be active')
    try:
        os.write(fd,str(os.getpid()).encode()); os.close(fd); run()
    finally: LOCK.unlink(missing_ok=True)
