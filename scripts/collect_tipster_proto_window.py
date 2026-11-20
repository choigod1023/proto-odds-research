"""Bounded public archive GETs with the existing client's required AJAX header.

No DB access, auth, redirects, retries, or generator runs. Prices are retrospective.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from wisetoto import _SEQ_RE, _decode, parse_rows
from matches import clean_team
from bets import winner_index


def collect(rounds):
    if not 1 <= len(rounds) <= 10:
        raise ValueError('choose 1..10 explicit rounds')
    probes=[];offers=[]
    session=requests.Session()
    session.headers.update({'User-Agent':'sportstoto-research/1.0','Referer':'https://www.wisetoto.com/index.htm',
                            'X-Requested-With':'XMLHttpRequest'})
    def get(url,params=None):
        t=datetime.now(timezone.utc).isoformat()
        response=session.get(url,params=params,timeout=(5,12),allow_redirects=False)
        probes.append(dict(url=response.url,observed_at=t,status=response.status_code,
                           sha256=hashlib.sha256(response.content).hexdigest()))
        response.raise_for_status()
        if response.status_code!=200:raise ValueError('unexpected status; redirects not followed')
        return response
    for year,rnd in rounds:
        if not (2023<=year<=2026 and 1<=rnd<=160):raise ValueError('invalid archive scope')
        try:
            index=get('https://www.wisetoto.com/index.htm',dict(tab_type='proto',game_type='pt',game_category='pt1',game_year=year,game_round=rnd))
            match=_SEQ_RE.search(_decode(index))
            if not match or match[1]!=str(year) or int(match[2])!=rnd or match[3]=='0':
                raise ValueError('index did not identify the requested archive')
            time.sleep(2.5)
            response=get('https://www.wisetoto.com/util/gameinfo/get_proto_list.htm',dict(game_category='pt1',game_year=year,game_round=rnd,game_month='',game_day='',game_info_master_seq=match[3],sports='',sort='',tab_type='proto'))
            rows=parse_rows(_decode(response),year,rnd);probes[-1]['parsed_rows']=len(rows)
            for g in rows:
                if g.league not in {'K리그1','K리그2','KBO','NPB','MLB','NBA','KBL'} or g.market_family not in {'승패','승무패'} or g.market_label or g.is_void:continue
                t=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',g.date_text)
                hs=re.search(r'(\d+)\s*$',g.home);aws=re.match(r'^\s*(\d+)',g.away)
                wi=winner_index(g.n_way,g.result)
                if not t or not hs or not aws or wi is None:continue
                month,day,hour,minute=map(int,t.groups()); y=year-1 if rnd==1 and month==12 else year
                offers.append(dict(league=g.league,date=f'{y}-{month:02}-{day:02}',
                    kickoff=f'{y}-{month:02}-{day:02}T{hour:02}:{minute:02}:00+09:00',
                    home=clean_team(g.home),away=clean_team(g.away),home_score=int(hs[1]),away_score=int(aws[1]),
                    odds=g.odds,winner=wi,source_url=response.url,
                    quote_quality='retrospective_archive_not_purchase_time_certified',
                    raw_year=year,raw_round=rnd,raw_game_no=g.game_no))
            time.sleep(2.5)
        except (requests.RequestException,ValueError) as exc:
            probes.append(dict(year=year,round=rnd,error=type(exc).__name__,message=str(exc)[:300]))
            break  # stop the host on errors; never try an alternate bypass
    return offers,probes


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--round',action='append',required=True)
    p.add_argument('--prefix',type=Path,required=True);a=p.parse_args()
    rows,probes=collect([tuple(map(int,x.split(':'))) for x in a.round])
    a.prefix.parent.mkdir(parents=True,exist_ok=True)
    for suffix,value in [('offers',rows),('probes',probes)]:
        a.prefix.with_name(a.prefix.name+'-'+suffix+'.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(offers=len(rows),requests=sum('status' in r for r in probes),dates=sorted({r['date'] for r in rows})),ensure_ascii=False))


if __name__=='__main__':main()
