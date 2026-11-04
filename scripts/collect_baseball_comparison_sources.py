"""Bounded KBO official schedule and lineup capture, with immutable raw evidence."""
import argparse, gzip, hashlib, importlib.util, json, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('kbo_probe', ROOT / 'experiments/kbo-m0-followup-20261103/sources/probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
def sha(b): return hashlib.sha256(b).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def fetch(evidence, name, url, params, session=None):
    evidence.mkdir(parents=True, exist_ok=True)
    meta = evidence / (name + '.json')
    if meta.exists():
        record = json.loads(meta.read_text(encoding='utf8'))
        if record['url'] != url or record['params'] != params: raise ValueError('cache request mismatch')
        if record['http_status'] != 200: raise ValueError('cached HTTP '+str(record['http_status']))
        raw = gzip.decompress((evidence / record['body_file']).read_bytes())
        if sha(raw) != record['body_sha256']: raise ValueError('cache hash mismatch')
        return raw, record
    sess = session or requests.Session()
    requested_at = now()
    response = sess.post(url, data=params, timeout=(5,15), allow_redirects=False, headers={'User-Agent':'Mozilla/5.0','Referer':probe.BASE+'/Schedule/GameCenter/Main.aspx'})
    raw = response.content; received_at = now(); digest = sha(raw)
    filename = digest + '.body.gz'; path = evidence / filename
    if not path.exists(): path.write_bytes(gzip.compress(raw, mtime=0))
    record = dict(name=name,url=url,params=params,requested_at=requested_at,received_at=received_at,http_status=response.status_code,body_sha256=digest,body_file=filename)
    meta.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    if response.status_code != 200: raise ValueError('HTTP '+str(response.status_code))
    return raw, record

def records(schedule_raw, schedule_meta, lineup_raw=None, lineup_meta=None):
    data=json.loads(schedule_raw)
    if str(data.get('code'))!='100' or not isinstance(data.get('game'),list): raise ValueError('invalid schedule')
    out=[]
    for g in data['game']:
        start=datetime.strptime(str(g['G_DT'])+' '+str(g['G_TM']), '%Y%m%d %H:%M').replace(tzinfo=probe.KST)
        receipt=datetime.fromisoformat(schedule_meta['received_at'])
        base=dict(sport='bs',league='KBO',kickoff=start.isoformat(),home=str(g['HOME_NM']).strip(),away=str(g['AWAY_NM']).strip())
        def make(kind,meta,payload):
            return dict(record_id=kind+':'+str(g['G_ID'])+':'+meta['body_sha256'][:16],kind=kind,**base,source_url=meta['url'],body_sha256=meta['body_sha256'],received_at=meta['received_at'],timing_basis='actual_receipt',payload={'source_fixture_id':str(g['G_ID']),**payload})
        out.append(make('fixture',schedule_meta,{'source_state':g.get('GAME_STATE_SC'),'source_cancel':g.get('CANCEL_SC_ID'),'starters':probe.starter_metadata(g)}))
        if lineup_raw is not None and lineup_meta is not None:
            parsed=probe.parse_lineup(lineup_raw,g)
            parsed['pregame_receipt']=datetime.fromisoformat(lineup_meta['received_at']) < start
            parsed['schedule_pregame_receipt']=receipt < start
            out.append(make('lineup',lineup_meta,parsed))
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if len(a.date)!=8 or not a.date.isdigit(): p.error('date must be YYYYMMDD')
    raw,meta=fetch(a.evidence,'schedule-'+a.date,probe.SCHEDULE,{'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':a.date})
    games=json.loads(raw)['game']; all_records=[]
    for g in games:
        if str(g.get('G_DT')) != a.date: raise ValueError('date mismatch')
        if not str(g.get('G_ID','')).isalnum(): raise ValueError('invalid official game id')
        # At most one game is examined, avoiding unbounded collection.
        if len(games)!=1: break
        time.sleep(1.5)
        lr,lm=fetch(a.evidence,'lineup-'+str(g['G_ID']),probe.LINEUP,{'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':g['G_ID']})
        all_records=records(raw,meta,lr,lm)
    if not all_records: all_records=records(raw,meta)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    if a.output.exists(): raise FileExistsError(a.output)
    a.output.write_text(json.dumps(all_records,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'records':len(all_records),'fixtures':len(games),'evidence':str(a.evidence)},ensure_ascii=False))
if __name__=='__main__': main()
