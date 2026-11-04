"""Finite direct KBO readiness probe; never produces a forecast.

No existing collector state is modified. Each HTTP request runs in a child with a
15-second wall-clock limit; 403/429 stops the probe. Raw bytes are archived.
"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.kbo_forward.official import SCHEDULE, LINEUP, KST, parse_lineup, SourceError

CHILD = r'''import base64,json,sys,requests
x=json.load(sys.stdin)
r=requests.post(x["url"],data=x["params"],headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx"},timeout=(5,10),allow_redirects=False)
print(json.dumps({"status":r.status_code,"body":base64.b64encode(r.content).decode()}))'''

def stamp():
    return datetime.now(timezone.utc).isoformat()

def probe(output, days=2):
    output.mkdir(parents=True, exist_ok=True)
    receipts, games, blockers = [], [], []
    stopped = False
    def fetch(url, params):
        nonlocal stopped
        if stopped or len(receipts) >= 8:
            return None
        rec = {'url': url, 'params': params, 'requested_at': stamp()}
        try:
            proc = subprocess.run([sys.executable, '-c', CHILD], input=json.dumps({'url':url,'params':params}), text=True, capture_output=True, timeout=15, check=True)
            rec['finished_at'] = stamp()
            reply = json.loads(proc.stdout)
            raw = base64.b64decode(reply['body'], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            (output / (digest + '.bin')).write_bytes(raw)
            rec.update(status=reply['status'], sha256=digest, body_file=digest+'.bin')
            if rec['status'] in (403,429): stopped = True
            receipts.append(rec)
            return raw if rec['status'] == 200 else None
        except (subprocess.SubprocessError, ValueError, KeyError) as exc:
            rec.update(finished_at=stamp(), status=None, error=type(exc).__name__)
            receipts.append(rec); stopped = True
            return None
    today = datetime.now(KST).date()
    for offset in range(days):
        day = today + timedelta(days=offset)
        params = {'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day.strftime('%Y%m%d')}
        raw = fetch(SCHEDULE, params)
        if raw is None:
            blockers.append('schedule_http_unavailable'); break
        try:
            data=json.loads(raw)
            if str(data.get('code')) != '100' or not isinstance(data.get('game'),list): raise ValueError()
            for g in data['game']:
                if g.get('G_DT') != params['date'] or str(g.get('LE_ID')) != '1': raise ValueError()
                kickoff=datetime.strptime(g['G_DT']+' '+g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=KST)
                eligible=(str(g.get('GAME_STATE_SC'))=='1' and str(g.get('CANCEL_SC_ID'))=='0' and datetime.now(timezone.utc)<=kickoff.astimezone(timezone.utc)-timedelta(minutes=30))
                entry={'official_game_id':g['G_ID'],'kickoff':kickoff.isoformat(),'state':str(g.get('GAME_STATE_SC')),'before_t30':eligible,'announced_starter_ids': {s:str(g.get(p+'_PIT_P_ID')) if g.get('START_PIT_CK') in (1,True) and str(g.get(p+'_PIT_P_ID') or '').isdigit() else None for s,p in (('home','B'),('away','T'))},'schedule_receipt':len(receipts)-1}
                games.append(entry)
                if eligible:
                    raw_lineup=fetch(LINEUP,{'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':g['G_ID']})
                    entry['lineup_receipt']=len(receipts)-1 if raw_lineup is not None else None
                    if raw_lineup is not None:
                        try:
                            ordered=parse_lineup(raw_lineup,g)
                            entry['lineup_status']='announced_names_only_ids_unresolved' if ordered else 'not_announced'
                        except (SourceError, ValueError, KeyError, IndexError, TypeError) as exc:
                            entry['lineup_status']='invalid:'+str(exc)
                    else: entry['lineup_status']='http_unavailable'
                    entry['source_verified']=False
                    entry['blockers']=['ordered_batter_ids_not_bound','full_history_not_bound']
                    break
        except (ValueError, KeyError, TypeError):
            blockers.append('invalid_schedule'); break
        if any(g['before_t30'] for g in games) or stopped: break
    report={'started_at':receipts[0]['requested_at'] if receipts else stamp(),'finished_at':stamp(),'direct_http_count':len(receipts),'stop_on_403_429':stopped,'games':games,'blockers':blockers,'receipts':receipts,'forecast_generated':False,'cache_modified':False,'note':'This diagnostic does not resolve player IDs or authenticate history; source_verified remains false.'}
    (output/'probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    print(json.dumps(probe(args.output),ensure_ascii=False,indent=2))
