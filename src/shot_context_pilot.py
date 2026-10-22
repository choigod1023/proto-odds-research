"""Bounded StatsBomb research acquisition and pre-event context reconstruction.

Data attribution: StatsBomb Open Data, https://github.com/hudl/open-data.
Private raw responses only. No production imports, odds, or pregame claims.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

import requests

BASE = "https://raw.githubusercontent.com/hudl/open-data/master/data/"
REQUEST_CAP = 45
BYTE_CAP = 100_000_000


class Fetcher:
    def __init__(self, db):
        self.db, self.requests, self.bytes = db, 0, 0
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'proto-odds-research/noncommercial-pilot'

    def get(self, suffix):
        if self.requests >= REQUEST_CAP or self.bytes >= BYTE_CAP:
            raise RuntimeError('request/byte budget reached')
        if '..' in suffix or not suffix.endswith('.json') or suffix.startswith('/'):
            raise ValueError('invalid endpoint')
        url = BASE+suffix
        self.requests += 1
        time.sleep(.3)
        body = bytearray()
        with self.session.get(url, timeout=(15,45), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                with self.db:
                    self.db.execute('INSERT INTO raw(url,received_at,status,sha256,body) VALUES(?,?,?,?,?)',
                                    (url,datetime.now(timezone.utc).isoformat(),response.status_code,'',b''))
                raise RuntimeError(f'HTTP {response.status_code}; no retry')
            for chunk in response.iter_content(65536):
                self.bytes += len(chunk)
                if self.bytes > BYTE_CAP or len(body)+len(chunk) > 12_000_000:
                    raise RuntimeError('response/batch byte budget reached')
                body.extend(chunk)
        payload = bytes(body)
        with self.db:
            self.db.execute('INSERT INTO raw(url,received_at,status,sha256,body) VALUES(?,?,?,?,?)',
                            (url,datetime.now(timezone.utc).isoformat(),200,hashlib.sha256(payload).hexdigest(),payload))
        return json.loads(payload)


def extract(match, events):
    home,away = match['home_team']['home_team_id'],match['away_team']['away_team_id']
    teams = (home,away)
    scores = {t:0 for t in teams}; reds = {t:0 for t in teams}
    sent_off = set(); shots=[]; audit=Counter(); ids=set(); indices=set(); active={}
    for e in sorted(events,key=lambda e:e['index']):
        if e['id'] in ids or e['index'] in indices:
            raise ValueError('duplicate event id/index')
        ids.add(e['id']); indices.add(e['index'])
        if e['period'] not in (1,2):
            audit['non_regulation_events'] +=1
            continue
        kind=e['type']['name']; team=e.get('team',{}).get('id')
        if team not in teams:
            if kind in ('Shot','Own Goal Against','Own Goal For','Bad Behaviour','Foul Committed'):
                raise ValueError('unknown event team')
            continue
        other=away if team==home else home
        if kind=='Starting XI':
            active[team]={p['player']['id'] for p in e['tactics']['lineup']}
        elif kind=='Substitution':
            if team not in active:raise ValueError('substitution without starting lineup')
            active[team].discard(e['player']['id'])
            active[team].add(e['substitution']['replacement']['id'])
        if kind=='Shot':
            shot=e['shot']; xg=shot.get('statsbomb_xg')
            if isinstance(xg,bool) or not isinstance(xg,(int,float)) or not math.isfinite(xg) or not 0<=xg<=1:
                raise ValueError('missing/invalid xG is not zero')
            location=e.get('location')
            if not isinstance(location,list) or len(location)<2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in location[:2]):
                raise ValueError('invalid shot location')
            outcome=shot.get('outcome',{}).get('name')
            if not outcome:
                raise ValueError('missing shot outcome')
            # Score/card state precedes this shot, even when this shot is a goal.
            shots.append(dict(event_id=e['id'],match_id=match['match_id'],day=match['match_date'],
                team=team,home=int(team==home),period=e['period'],minute=e['minute'],second=e['second'],
                x=float(location[0]),y=float(location[1]),xg=xg,goal=int(outcome=='Goal'),
                penalty=shot.get('type',{}).get('name')=='Penalty',
                body_part=shot.get('body_part',{}).get('name'),
                play_pattern=e.get('play_pattern',{}).get('name'),
                score_diff=scores[team]-scores[other],man_diff=reds[other]-reds[team],
                under_pressure=e.get('under_pressure',False),
                freeze_frame_players=len(shot.get('freeze_frame') or [])))
            if outcome=='Goal': scores[team]+=1
        elif kind=='Own Goal Against':
            scores[other]+=1; audit['own_goals_against']+=1
        elif kind=='Own Goal For':
            audit['own_goals_for_ignored_pair']+=1
        for field in ('foul_committed','bad_behaviour'):
            card=e.get(field,{}).get('card',{}).get('name')
            if card in ('Red Card','Second Yellow'):
                player=e.get('player',{}).get('id')
                if player is None: raise ValueError('red card without player')
                key=(team,player)
                if key not in sent_off:
                    if team not in active:raise ValueError('red card without known active lineup')
                    if player not in active[team]:
                        audit['off_field_dismissals']+=1
                        continue
                    active[team].remove(player)
                    sent_off.add(key);reds[team]+=1; audit['dismissals']+=1
    expected=[match['home_score'],match['away_score']]
    actual=[scores[home],scores[away]]
    if actual != expected:
        raise ValueError(f'score reconstruction mismatch: {actual} vs {expected}')
    audit['shots']=len(shots)
    audit['goals']=sum(s['goal'] for s in shots)
    return shots,dict(audit)


def summarize(shots):
    groups=defaultdict(list)
    for s in shots:
        if s['penalty']:continue
        state='leading' if s['score_diff']>0 else 'trailing' if s['score_diff']<0 else 'tied'
        groups[(state,'equal' if s['man_diff']==0 else 'unequal')].append(s)
    return {':'.join(k):{'shots':len(v),'matches':len({s['match_id'] for s in v}),
            'mean_xg':sum(s['xg'] for s in v)/len(v),'goals':sum(s['goal'] for s in v),
            'goal_rate':sum(s['goal'] for s in v)/len(v)} for k,v in sorted(groups.items())}


def run(output, limit):
    if not 1<=limit<=40:raise ValueError('match limit must be 1..40')
    output.mkdir(parents=True,exist_ok=False)
    with closing(sqlite3.connect(output/'pilot.sqlite3')) as db:
        db.executescript('CREATE TABLE raw(url TEXT PRIMARY KEY,received_at TEXT,status INT,sha256 TEXT,body BLOB);'
                        'CREATE TABLE matches(id INT PRIMARY KEY,metadata TEXT,audit TEXT);'
                        'CREATE TABLE shots(id TEXT PRIMARY KEY,match_id INT,data TEXT);'
                        'CREATE TABLE run(status TEXT,summary TEXT);')
        db.execute("INSERT INTO run VALUES('running',NULL)");db.commit()
        fetch=Fetcher(db); allshots=[]; invalid=[]
        try:
            matches=fetch.get('matches/2/27.json')
            teams=Counter(t for m in matches for t in (m['home_team']['home_team_id'],m['away_team']['away_team_id']))
            if len(matches)!=380 or len(teams)!=20 or set(teams.values())!={38}:
                raise ValueError('unexpected source competition coverage')
            ordered=sorted(matches,key=lambda m:(m['match_date'],m['kick_off'],m['match_id']))[:limit]
            for m in ordered:
                events=fetch.get(f"events/{m['match_id']}.json")
                try: shots,audit=extract(m,events)
                except ValueError as exc:
                    invalid.append({'id':m['match_id'],'error':str(exc)});continue
                with db:
                    db.execute('INSERT INTO matches VALUES(?,?,?)',(m['match_id'],json.dumps(m),json.dumps(audit)))
                    db.executemany('INSERT INTO shots VALUES(?,?,?)',[(s['event_id'],m['match_id'],json.dumps(s)) for s in shots])
                allshots.extend(shots)
                print(json.dumps({'match':m['match_id'],'shots':len(shots),'bytes':fetch.bytes}),flush=True)
            summary={'source':'StatsBomb Open Data','league':'EPL','season':'2015/2016','source_season_games':380,
                     'requested_games':limit,'accepted_games':len(ordered)-len(invalid),'invalid_games':invalid,
                     'shots':len(allshots),'request_count':fetch.requests,'bytes':fetch.bytes,
                     'date_from':ordered[0]['match_date'],'date_to':ordered[-1]['match_date'],
                     'groups':summarize(allshots),'production_allowed':False,'evaluation':'context extraction pilot, not pregame accuracy',
                     'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
            with db:db.execute("UPDATE run SET status='complete',summary=?",(json.dumps(summary),))
        except Exception as exc:
            with db:db.execute("UPDATE run SET status='failed',summary=?",(json.dumps({'error':str(exc),'requests':fetch.requests,'bytes':fetch.bytes}),))
            raise
        finally:fetch.session.close()
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--limit',type=int,default=30)
    a=p.parse_args();print(json.dumps(run(a.output,a.limit)))
