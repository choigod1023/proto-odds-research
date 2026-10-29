"""One-shot append-only KBO/NPB public pregame capture; no runtime DB or scheduler."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys

import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from baseball_context_watch import API,KST,normalize_lineup,_extract_lineup
from kbo_official import collect_official


def complete_nine(players):
    batters=[p for p in players if str(p.get('batting_order')) in set(map(str,range(1,10)))]
    return (len(batters)==9 and sorted(int(p['batting_order']) for p in batters)==list(range(1,10))
        and len({p.get('player_code') or p.get('name') for p in batters})==9
        and all(p.get('name') for p in batters))


def capture(output):
    session=requests.Session();session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; proto-odds-research/1.0)','Referer':'https://m.sports.naver.com/'})
    now=datetime.now(timezone.utc);day=now.astimezone(KST).date();records=[];audit=[]
    for league,upper,category in [('KBO','kbaseball','kbo'),('NPB','wbaseball','npb')]:
        try:
            r=session.get(API,params=dict(fields='basic,statusNum,homeStarterName,awayStarterName',upperCategoryId=upper,
                categoryId=category,fromDate=day.isoformat(),toDate=(day+timedelta(days=1)).isoformat(),size=50),timeout=15)
            r.raise_for_status();games=r.json()['result']['games'];before=0
            for game in games:
                if game.get('statusCode')!='BEFORE':continue
                start=datetime.fromisoformat(game['gameDateTime'])
                if start.tzinfo is None:start=start.replace(tzinfo=KST)
                if datetime.now(timezone.utc)>=start:continue
                before+=1;url=f"{API}/{game['gameId']}/lineup"
                lr=session.get(url,timeout=15);lr.raise_for_status();payload=lr.json()['result'].get('lineUpData') or {}
                lineup=_extract_lineup(payload) or {};observed=datetime.now(timezone.utc)
                sides={s:normalize_lineup(lineup.get(s) or {}) for s in ['home','away']}
                starters={s:game.get(s+'StarterName') for s in ['home','away']}
                accepted=(observed<start and all(starters.values())
                    and all(sides[s]['confirmed'] and complete_nine(sides[s]['players']) for s in sides))
                records.append(dict(league=league,game_id=game['gameId'],home=game['homeTeamName'],away=game['awayTeamName'],
                    start_at=start.isoformat(),observed_at=observed.isoformat(),status=game['statusCode'],url=url,
                    source_kind='direct_pregame_capture',starters=starters,lineups=sides,accepted=bool(accepted),
                    reason='announced_both_teams' if accepted else 'missing_starter_or_lineup_or_fetch_crossed_start',
                    schedule_sha256=hashlib.sha256(r.content).hexdigest(),lineup_sha256=hashlib.sha256(lr.content).hexdigest()))
            audit.append(dict(league=league,schedule_rows=len(games),before_start_rows=before,ok=True))
        except Exception as e:
            audit.append(dict(league=league,route='naver',ok=False,error_type=type(e).__name__,reason=str(e)[:140]))
            if league=='KBO':
                try:
                    # Existing official parser checks LINEUP_CK, exact game/team IDs,
                    # and excludes yesterday's lineup. Empty previous state forbids carryover.
                    doc=collect_official(existing={})
                    for r in doc['games']:
                        observed=datetime.now(timezone.utc);start=datetime.fromisoformat(r['game_datetime'])
                        accepted=(observed<start and r['lineup_status']['official_today']
                            and r['starter_status']['state']=='announced' and not r['collection']['failed_endpoints']
                            and all(complete_nine(r['lineups'].get(s) or []) for s in ['home','away']))
                        records.append(dict(league='KBO',game_id=r['game_id'],start_at=r['game_datetime'],observed_at=observed.isoformat(),
                            source_kind='direct_official_pregame_capture',url=r['source_url'],accepted=bool(accepted),
                            starters=r['starters'],lineups=r['lineups'],reason='announced_both_teams' if accepted else 'not_yet_complete',
                            normalized_payload_sha256=hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True).encode()).hexdigest()))
                    audit.append(dict(league=league,route='kbo_official',ok=not doc['errors'],schedule_rows=len(doc['games'])))
                except Exception as fallback:
                    audit.append(dict(league=league,route='kbo_official',ok=False,error_type=type(fallback).__name__,reason=str(fallback)[:140]))
    output.mkdir(parents=True,exist_ok=True)
    with (output/'direct-pregame-captures.jsonl').open('a',encoding='utf8',newline='\n') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    with (output/'direct-capture-audit.jsonl').open('a',encoding='utf8',newline='\n') as f:
        f.write(json.dumps(dict(checked_at=now.isoformat(),sources=audit,captured=len(records),eligible=sum(r['accepted'] for r in records)),ensure_ascii=False)+'\n')
    print(json.dumps(dict(sources=audit,captured=len(records),eligible=sum(r['accepted'] for r in records)),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);capture(p.parse_args().output)
