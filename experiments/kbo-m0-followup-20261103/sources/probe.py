"""Bounded public KBO schedule/lineup metadata probe; no prediction or ledger."""
import gzip,hashlib,json,time
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
BASE='https://www.koreabaseball.com'; SCHEDULE=BASE+'/ws/Main.asmx/GetKboGameList';LINEUP=BASE+'/ws/Schedule.asmx/GetLineUpAnalysis'
KST=timezone(timedelta(hours=9));CAP=20;DELAY=1.5
DATES=[date(2026,10,28)+timedelta(days=i) for i in range(14)]
def sha(b):return hashlib.sha256(b).hexdigest()
def write(path,data):path.write_bytes((json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
class Stop(Exception):pass

class Fetcher:
    def __init__(self):
        (ROOT/'evidence').mkdir(exist_ok=True);self.path=ROOT/'requests.json';self.records=json.loads(self.path.read_bytes()) if self.path.exists() else []
        self.session=requests.Session();self.session.headers.update({'User-Agent':'Mozilla/5.0','Referer':BASE+'/Schedule/GameCenter/Main.aspx'})
        self.count=sum(x.get('origin')=='direct' for x in self.records);self.last=0
        direct=[x for x in self.records if x.get('origin')=='direct']
        if direct:
            stamp=direct[-1].get('received_at') or direct[-1].get('failed_at');elapsed=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds());self.last=time.monotonic()-elapsed
    def fetch(self,name,url,params):
        old=next((x for x in reversed(self.records) if x['name']==name and x.get('http_status')==200),None)
        if old:
            raw=gzip.decompress((ROOT/old['body_file']).read_bytes())
            if sha(raw)!=old['sha256']:raise Stop('cached_body_sha_mismatch')
            return raw,old
        if any(x.get('http_status')==403 for x in self.records):raise Stop('previous_403')
        for attempt in range(2):
            if self.count>=CAP:raise Stop('request_cap')
            time.sleep(max(0,DELAY-(time.monotonic()-self.last)));self.count+=1
            rec={'name':name,'origin':'direct','method':'POST','url':url,'params':params,'attempt':attempt+1,'requested_at':datetime.now(timezone.utc).isoformat(),'timeout_connect_seconds':5,'timeout_read_seconds':15}
            try:
                resp=self.session.post(url,data=params,timeout=(5,15),allow_redirects=False);raw=resp.content;rec.update(http_status=resp.status_code,received_at=datetime.now(timezone.utc).isoformat(),sha256=sha(raw),body_file='evidence/'+sha(raw)+'.body.gz',content_type=resp.headers.get('Content-Type'));(ROOT/rec['body_file']).write_bytes(gzip.compress(raw,mtime=0))
            except requests.RequestException as exc:
                resp=None;rec.update(http_status=None,failed_at=datetime.now(timezone.utc).isoformat(),error_type=type(exc).__name__)
            self.records.append(rec);write(self.path,self.records);self.last=time.monotonic()
            if resp is not None and resp.status_code==200:return raw,rec
            if resp is not None and resp.status_code==403:raise Stop('http_403')
            if resp is not None and resp.status_code!=429:raise Stop('http_'+str(resp.status_code))
            if attempt==0:time.sleep(15)
        raise Stop('timeout_or_429_after_backoff')

def classify(game,receipt):
    cancel=game.get('CANCEL_SC_ID');state=game.get('GAME_STATE_SC');result=game.get('GAME_RESULT_CK')
    if cancel is None:return 'unknown_missing_cancel_code'
    if str(cancel)!='0':return 'cancelled_or_suspended_source_code'
    if str(state)=='3' and str(result)=='1':return 'completed'
    if str(state)=='1':
        try:start=datetime.strptime(str(game['G_DT'])+' '+str(game['G_TM']),'%Y%m%d %H:%M').replace(tzinfo=KST)
        except (KeyError,ValueError,TypeError):return 'unknown_missing_kickoff'
        return 'future' if start>datetime.fromisoformat(receipt) else 'pending_past_kickoff'
    return 'unknown_or_in_progress'

def parse_lineup(body,game):
    obj=json.loads(body)
    if not isinstance(obj,list) or len(obj)!=5 or not obj[0] or type(obj[0][0].get('LINEUP_CK')) is not bool:raise ValueError('invalid_lineup_envelope')
    if obj[0][0]['LINEUP_CK'] is False:return {'status':'not_confirmed','ordered_nine':None}
    out={}
    for side,meta_i,table_i in [('home',1,3),('away',2,4)]:
        meta=obj[meta_i][0]
        for field,val in [('G_ID',game['G_ID']),('T_ID',game[side.upper()+'_ID']),('LE_ID',game['LE_ID']),('SR_ID',game['SR_ID']),('SEASON_ID',game['SEASON_ID'])]:
            if str(meta.get(field))!=str(val):raise ValueError('lineup_identity_mismatch_'+field)
        rows=json.loads(obj[table_i][0])['rows'];players=[{'slot':int(x['row'][0]['Text']),'position':str(x['row'][1]['Text']).strip(),'name':str(x['row'][2]['Text']).strip()} for x in rows]
        if sorted(x['slot'] for x in players)!=list(range(1,10)) or any(not x['name'] or not x['position'] for x in players):raise ValueError('incomplete_ordered_nine')
        out[side]=sorted(players,key=lambda x:x['slot'])
    return {'status':'confirmed','ordered_nine':out}

def starter_metadata(g):
    flag=g.get('START_PIT_CK');both={}
    for side,prefix in [('home','B'),('away','T')]:
        pid=g.get(prefix+'_PIT_P_ID');name=str(g.get(prefix+'_PIT_P_NM') or '').strip()
        if flag in (1,True) and str(pid or '').isdigit() and int(pid)>0 and name:both[side]={'player_id':str(pid),'name':name}
    return {'source_flag':flag,'status':'announced_both' if len(both)==2 else 'not_confirmed_both','announced':both}

def main():
    f=Fetcher();games=[];day_status=[];stopped=None
    for d in DATES:
        day=d.strftime('%Y%m%d');params={'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day}
        try:
            raw,rec=f.fetch('schedule-'+day,SCHEDULE,params);body=json.loads(raw)
            if str(body.get('code'))!='100' or not isinstance(body.get('game'),list):raise ValueError('invalid_schedule_envelope')
            day_status.append({'date':d.isoformat(),'status':'success','count':len(body['game']),'received_at':rec['received_at'],'response_sha256':rec['sha256']})
            for g in body['game']:
                if str(g.get('G_DT'))!=day or not g.get('G_ID'):raise ValueError('schedule_game_date_or_id_mismatch')
                games.append({'official_game_id':g['G_ID'],'date':d.isoformat(),'kickoff_local':g.get('G_TM'),'timezone':'Asia/Seoul','home_id':g.get('HOME_ID'),'away_id':g.get('AWAY_ID'),'home_name':str(g.get('HOME_NM') or '').strip(),'away_name':str(g.get('AWAY_NM') or '').strip(),'season_id':g.get('SEASON_ID'),'league_id':g.get('LE_ID'),'series_id':g.get('SR_ID'),'header_no':g.get('HEADER_NO'),'state_code':g.get('GAME_STATE_SC'),'result_code':g.get('GAME_RESULT_CK'),'cancel_code':g.get('CANCEL_SC_ID'),'classification':classify(g,rec['received_at']),'starter':starter_metadata(g),'lineup':{'status':'not_checked'},'received_at':rec['received_at'],'schedule_sha256':rec['sha256']})
        except (Stop,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
            day_status.append({'date':d.isoformat(),'status':'failed','reason':str(exc)[:100]});stopped=str(exc)[:100];break
    if len({g['official_game_id'] for g in games})!=len(games):raise ValueError('duplicate_official_game_id')
    futures=sorted((g for g in games if g['classification']=='future'),key=lambda g:(g['date'],g['kickoff_local'],g['official_game_id']))
    for g in futures:
        params={'leId':g['league_id'],'srId':g['series_id'],'seasonId':g['season_id'],'gameId':g['official_game_id']}
        name='lineup-'+g['official_game_id']
        try:
            raw,rec=f.fetch(name,LINEUP,params);source={'G_ID':g['official_game_id'],'HOME_ID':g['home_id'],'AWAY_ID':g['away_id'],'LE_ID':g['league_id'],'SR_ID':g['series_id'],'SEASON_ID':g['season_id']}
            g['lineup']=parse_lineup(raw,source)|{'received_at':rec['received_at'],'response_sha256':rec['sha256']}
        except (Stop,ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError) as exc:
            g['lineup']={'status':'not_checked_budget' if str(exc)=='request_cap' else 'fetch_or_parse_failed','reason':str(exc)[:100]}
            if isinstance(exc,Stop):stopped=str(exc);break
    from collections import Counter
    totals=Counter(g['classification'] for g in games)
    counts={name:totals[name] for name in ('completed','future','cancelled_or_suspended_source_code','pending_past_kickoff','unknown_missing_cancel_code','unknown_missing_kickoff','unknown_or_in_progress')}
    lineups=dict(Counter(g['lineup']['status'] for g in futures))
    out={'window_start':'2026-10-28','window_end':'2026-11-10','timezone':'Asia/Seoul','day_status':day_status,'games':games,'counts':counts,'future_lineup_status':lineups,'failed_or_unobserved_days':14-sum(x['status']=='success' for x in day_status),'direct_requests':f.count,'request_cap':CAP,'stop_reason':stopped,'price_source_connected':False,'prediction_executed':False,'limitations':['Received times are current UTC, not historical pregame receipt.','This bounded window does not establish season end.','No complete odds connection or simulated prediction was performed.']}
    write(ROOT/'observations.json',out)
    manifest={'status':'complete_window' if out['failed_or_unobserved_days']==0 else 'partial_window','observations_sha256':sha((ROOT/'observations.json').read_bytes()),'requests_sha256':sha((ROOT/'requests.json').read_bytes()),'counts':counts,'future_lineup_status':lineups,'direct_requests':f.count,'failed_or_unobserved_days':out['failed_or_unobserved_days']}
    write(ROOT/'manifest.json',manifest);print(json.dumps(manifest,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
