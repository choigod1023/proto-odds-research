"""Fetch NBA official date pages that expose final game-card facts in SSR JSON."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests

from scripts.run_tipster_year_audit import read

NAMES = {
 'ATL':'Atlanta Hawks','BOS':'Boston Celtics','BKN':'Brooklyn Nets','CHA':'Charlotte Hornets',
 'CHI':'Chicago Bulls','CLE':'Cleveland Cavaliers','DAL':'Dallas Mavericks','DEN':'Denver Nuggets',
 'DET':'Detroit Pistons','GSW':'Golden State Warriors','HOU':'Houston Rockets','IND':'Indiana Pacers',
 'LAC':'Los Angeles Clippers','LAL':'Los Angeles Lakers','MEM':'Memphis Grizzlies','MIA':'Miami Heat',
 'MIL':'Milwaukee Bucks','MIN':'Minnesota Timberwolves','NOP':'New Orleans Pelicans','NYK':'New York Knicks',
 'OKC':'Oklahoma City Thunder','ORL':'Orlando Magic','PHI':'Philadelphia 76ers','PHX':'Phoenix Suns',
 'POR':'Portland Trail Blazers','SAC':'Sacramento Kings','SAS':'San Antonio Spurs','TOR':'Toronto Raptors',
 'UTA':'Utah Jazz','WAS':'Washington Wizards',
}


def parse_page(content, day, observed_at, diagnostics=None):
    node = BeautifulSoup(content, 'html.parser').find('script', id='__NEXT_DATA__')
    if node is None:
        raise ValueError('official SSR data absent')
    p = json.loads(node.get_text())['props']['pageProps']
    if p.get('hasError') or p.get('selectedDate') != day:
        raise ValueError('requested date not honored')
    output = []
    for module in p.get('gameCardFeed', {}).get('modules', []):
        for card in module.get('cards', []):
            if card.get('cardType')!='game':
                continue
            g = card['cardData']
            if g.get('leagueId')!='00' or g.get('gameStatus')!=3:
                continue
            utc = datetime.fromisoformat(g['gameTimeUtc'].replace('Z','+00:00'))
            # NBA gameTimeEastern carries a literal Z although it is wall time.
            if g['gameTimeEastern'][:10] != day:
                raise ValueError('game card date mismatch')
            h, a = g['homeTeam'], g['awayTeam']
            if h['teamTricode'] not in NAMES or a['teamTricode'] not in NAMES:
                if diagnostics is not None:
                    diagnostics.append(dict(game_id=g['gameId'], reason='non_NBA_opponent_in_official_exhibition',
                        home=h['teamTricode'], away=a['teamTricode']))
                continue
            if type(h['score']) is not int or type(a['score']) is not int or h['score']==a['score']:
                raise ValueError('invalid final score')
            kst = utc.astimezone(timezone(timedelta(hours=9)))
            output.append(dict(league='NBA', official_id='nba:'+g['gameId'],
                match_date=kst.date().isoformat(), official_date=day,
                home=NAMES[h['teamTricode']], away=NAMES[a['teamTricode']],
                raw_team_names={'home':h['teamName'],'away':a['teamName']},
                home_score=h['score'], away_score=a['score'], kickoff=kst.isoformat(),
                source_url='https://www.nba.com/games?date='+day,
                game_url=g.get('shareUrl'), actual_end_utc=g.get('actualEndTimeUTC'),
                observed_at=observed_at, result_evidence='official_exact_fixture'))
    return output


def requested_dates(paths):
    days = set()
    for path in paths:
        for r in read(path):
            if r.get('league')!='NBA':
                continue
            ts = r.get('source_reported_game_time_utc')
            if ts:
                d = datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
            else:
                d = r.get('match_date')
            if d and '2025-10-27'<=d<='2026-10-27':
                days.add(d)
    return sorted(days)


def collect(paths, out):
    out.mkdir(parents=True, exist_ok=True)
    lp, rp = out/'nba-date-requests.json', out/'nba-results.json'
    log = json.loads(lp.read_text(encoding='utf8')) if lp.exists() else []
    rows = json.loads(rp.read_text(encoding='utf8')) if rp.exists() else []
    days = requested_dates(paths)
    (out/'nba-requested-dates.json').write_text(json.dumps(days,indent=2)+'\n',encoding='utf8')
    session = requests.Session()
    session.headers.update({'User-Agent':'sportstoto-research/1.0','Referer':'https://www.nba.com/'})
    for day in days:
        if any(r['date']==day and not str(r.get('error','')).startswith("KeyError: 'LON'") for r in log):
            continue
        url = 'https://www.nba.com/games?date='+day
        item = dict(date=day,url=url,observed_at=datetime.now(timezone.utc).isoformat())
        try:
            response = session.get(url,timeout=(4,12),allow_redirects=False)
            item.update(status=response.status_code,sha256=hashlib.sha256(response.content).hexdigest())
            response.raise_for_status()
            if response.status_code!=200:
                raise ValueError('unexpected redirect')
            item['excluded_games'] = []
            batch = parse_page(response.content,day,item['observed_at'],item['excluded_games'])
            rows.extend(batch)
            item['official_finals'] = len(batch)
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            item['error']=f'{type(exc).__name__}: {exc}'[:300]
        log.append(item)
        lp.write_text(json.dumps(log,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        rp.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(f'{day} finals={item.get("official_finals")} total={len(rows)} {item.get("error","")}',flush=True)
        if item.get('status') in {401,403,429}:
            break
        time.sleep(1)
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',type=Path,action='append',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    collect(a.candidate,a.out)
