"""Official NPB past boxscores for local relief-workload validation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone
from pathlib import Path
import argparse,gzip,hashlib,json,re,time

import requests
from bs4 import BeautifulSoup

CODES=dict(f='닛폰햄',e='라쿠텐',g='요미우리',d='주니치',h='소프트뱅크',m='지바롯데',
           l='세이부',b='오릭스',s='야쿠르트',c='히로시마',t='한신',db='요코하마')
NAMES={'北海道日本ハムファイターズ':'닛폰햄','東北楽天ゴールデンイーグルス':'라쿠텐','読売ジャイアンツ':'요미우리',
       '中日ドラゴンズ':'주니치','福岡ソフトバンクホークス':'소프트뱅크','千葉ロッテマリーンズ':'지바롯데',
       '埼玉西武ライオンズ':'세이부','オリックス・バファローズ':'오릭스','東京ヤクルトスワローズ':'야쿠르트',
       '広島東洋カープ':'히로시마','阪神タイガース':'한신','横浜DeNAベイスターズ':'요코하마'}


def canonical(text):
    text=re.sub(r'\s+','',text)
    for name,team in NAMES.items():
        if re.sub(r'\s+','',name) in text:return team
    raise ValueError('unknown_official_team:'+text)


def outs(value):
    text=re.sub(r'\s+','',str(value)).removesuffix('+');fraction=1 if '⅓' in text else 2 if '⅔' in text else 0
    text=text.replace('⅓','').replace('⅔','')
    if '.' in text:
        whole,tail=text.split('.')
        if tail not in {'0','1','2'}:raise ValueError('invalid_baseball_innings')
        return 3*int(whole or '0')+int(tail)
    return 3*int(text or '0')+fraction


def parse_box(html,url):
    match=re.search(r'/scores/(\d{4})/(\d{2})(\d{2})/([a-z]+)-([a-z]+)-(\d+)/',url)
    if not match or match[4] not in CODES or match[5] not in CODES:raise ValueError('not_first_team_fixture')
    home,away=CODES[match[4]],CODES[match[5]];soup=BeautifulSoup(html,'lxml');scores={};pitchers={}
    if '【試合終了】' not in soup.get_text():raise ValueError('no_official_final_status')
    for table in soup.find_all('table'):
        first=table.find('tr')
        if not first:continue
        header=[c.get_text(' ',strip=True) for c in first.find_all(['th','td'],recursive=False)]
        if '計' in header and 'H' in header and 'E' in header:
            column=header.index('計')
            for tr in table.find_all('tr')[1:]:
                cells=tr.find_all(['th','td'],recursive=False)
                if len(cells)>column:scores[canonical(cells[0].get_text(' ',strip=True))]=int(cells[column].get_text(strip=True))
        if '投球数' not in first.get_text() or '自責点' not in first.get_text():continue
        team=canonical(table.find_previous(['h3','h4','h5']).get_text(' ',strip=True));rows=[]
        for tr in table.find_all('tr'):
            if tr.find_parent('table') is not table:continue
            cells=tr.find_all(['th','td'],recursive=False)
            if len(cells)!=14:continue
            link=cells[1].find('a',href=True)
            if not link:continue
            values=[c.get_text(' ',strip=True) for c in cells]
            pid=re.search(r'/players/(\d+)\.html',link['href'])
            if not pid:raise ValueError('missing_pitcher_identity')
            rows.append(dict(player='npb:'+pid[1],name=values[1],outs=outs(values[4]),runs=int(values[12]),
                bb=int(values[7]),k=int(values[9]),pitches=int(values[2]),bf=int(values[3]),hits=int(values[5])))
        pitchers[team]=rows
    if any(t not in pitchers or not pitchers[t] or t not in scores for t in [home,away]):raise ValueError('missing_final_pitching_or_score')
    if sum(p['runs'] for p in pitchers[home])!=scores[away] or sum(p['runs'] for p in pitchers[away])!=scores[home]:raise ValueError('pitching_runs_do_not_match_official_score')
    return dict(league='NPB',date=f'{match[1]}-{match[2]}-{match[3]}',home=home,away=away,hg=scores[home],ag=scores[away],
        official_id=f'{match[1]}{match[2]}{match[3]}:{match[4]}:{match[5]}:{match[6]}',pitchers=[pitchers[home],pitchers[away]],
        source_kind='past_official_box_only_not_current_announcement',url=url,accepted=True)


def get(url):
    for attempt in range(2):
        try:
            r=requests.get(url,timeout=30);r.raise_for_status();r.encoding=r.apparent_encoding;return r
        except requests.RequestException:
            if attempt:raise
            time.sleep(.5)


def collect(output,retry_failed=False):
    output.mkdir(parents=True,exist_ok=True);sources=output/'sources';sources.mkdir(exist_ok=True)
    for year,first,last in [(2024,'2024-03-29','2024-11-30'),(2026,'2026-03-27','2026-09-16')]:
        urls=set();schedule_provenance=[]
        for month in range(3,int(last[5:7])+1):
            url=f'https://npb.jp/games/{year}/schedule_{month:02d}_detail.html';r=get(url);s=BeautifulSoup(r.text,'lxml')
            schedule_provenance.append(dict(url=url,response_sha256=hashlib.sha256(r.content).hexdigest()))
            for a in s.find_all('a',href=True):
                m=re.fullmatch(fr'/scores/{year}/(\d{{2}})(\d{{2}})/([a-z]+)-([a-z]+)-(\d+)/',a['href'])
                if m and m[3] in CODES and m[4] in CODES and first<=f'{year}-{m[1]}-{m[2]}'<=last:urls.add('https://npb.jp'+a['href']+'box.html')
        path=sources/f'npb-{year}-boxes.jsonl';done=set()
        if path.exists():
            latest={r['url']:r for r in (json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s)}
            done={url for url,r in latest.items() if r['accepted'] or not retry_failed}
        def one(url):
            try:
                r=get(url);fact=parse_box(r.text,url)
                return dict(fact,response_sha256=hashlib.sha256(r.content).hexdigest(),collected_at=datetime.now(timezone.utc).isoformat())
            except Exception as e:return dict(url=url,accepted=False,reason=str(e)[:160])
        print('NPB scheduled',year,len(urls),'cached',len(done),flush=True)
        with path.open('a',encoding='utf8',newline='\n') as f,ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(one,u) for u in sorted(urls-done)]
            for i,future in enumerate(as_completed(futures),1):
                f.write(json.dumps(future.result(),ensure_ascii=False)+'\n');f.flush()
                if i%100==0:print('NPB',year,len(done)+i,'/',len(urls),flush=True)
        (sources/f'npb-{year}-schedules.json').write_text(json.dumps(schedule_provenance,indent=2)+'\n',encoding='utf8',newline='\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--retry-failed',action='store_true')
    a=p.parse_args();collect(a.output,a.retry_failed)
