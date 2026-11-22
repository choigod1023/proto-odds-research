"""Bounded 2025-only KBO identity capture, separate from production and model data."""
import gzip,hashlib,json,os,re,sys,time
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import requests
from lineup_reference import parse_lineup, profile_name, select_schedule_game

HERE=Path(__file__).resolve().parent
STATE=HERE/'capture-state'
ROWS=Path('C:/Users/user/Documents/Codex/kbodata/experiments/kbo-player-data-20261103/rows.jsonl')
BATTERS=Path('C:/Users/user/Documents/Codex/kbodata/data/raw/detail/kbo_batters_indiv_2023_2026.json')
ROOT='https://www.koreabaseball.com'
SCHEDULE=ROOT+'/ws/Main.asmx/GetKboGameList';LINEUP=ROOT+'/ws/Schedule.asmx/GetLineUpAnalysis';BOX=ROOT+'/ws/Schedule.asmx/GetBoxScore';PROFILE=ROOT+'/Record/Player/HitterDetail/Basic.aspx'
EXPECTED='66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c'
BATTER_SHA='24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'
MAX=2400;DELAY=1.5
class Stop(Exception):pass
def sha(b):return hashlib.sha256(b).hexdigest()
def key(method,url,params):return sha(json.dumps([method,url,params],sort_keys=True,ensure_ascii=False).encode())
def fidpath(folder,fid):return STATE/folder/(sha(fid.encode())+'.json')
def write(p,data):
    p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp');tmp.write_bytes((json.dumps(data,ensure_ascii=False,sort_keys=True)+'\n').encode())
    # A concurrent reader can briefly deny replacement on Windows.
    for attempt in range(20):
        try:os.replace(tmp,p);return
        except PermissionError:
            if attempt==19:raise
            time.sleep(.1)
def read_cache_roots():
    found={};base=Path('C:/Users/user/Documents/Codex/klu/.research-state')
    for folder in sorted(base.glob('kbo-lineups*/requests')):
        for p in folder.glob('*.json'):
            try:
                m=json.loads(p.read_bytes())
                if m.get('http_status')!=200 or not m.get('sha256'):continue
                if m.get('url') not in (SCHEDULE,LINEUP,PROFILE):continue
                found[key(m['method'],m['url'],m['params'])]=(m,folder.parent/'raw'/m['sha256'])
            except (ValueError,KeyError,OSError):continue
    return found

class Capture:
    def __init__(self,progress):
        self.progress=progress;self.external=read_cache_roots();self.journal=STATE/'requests.jsonl';self.cache={};self.memory={};self.last=0;self.s=requests.Session();self.s.headers.update({'User-Agent':'Mozilla/5.0','Referer':ROOT+'/Schedule/GameCenter/Main.aspx'})
        (STATE/'raw').mkdir(parents=True,exist_ok=True)
        if self.journal.exists():
            for line in self.journal.read_bytes().splitlines():
                rec=json.loads(line)
                if rec.get('http_status')==200:self.cache[rec['request_key']]=rec
        previous=[x for x in self.records() if x.get('origin')=='new_request'];self.count=len(previous)
        if previous:
            stamp=previous[-1].get('received_at') or previous[-1].get('failed_at')
            elapsed=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds())
            self.last=time.monotonic()-elapsed
    def records(self):return [json.loads(x) for x in self.journal.read_bytes().splitlines()] if self.journal.exists() else []
    def update(self,**kw):
        self.progress.update(kw);self.progress.update(updated_at=datetime.now(timezone.utc).isoformat(),new_requests=self.count,request_cap=MAX);write(STATE/'progress.json',self.progress)
    def append(self,m,raw=None):
        if raw is not None:
            digest=sha(raw);p=STATE/'raw'/(digest+'.gz')
            if not p.exists():p.write_bytes(gzip.compress(raw,mtime=0))
            m.update(sha256=digest,raw_file='raw/'+p.name)
        with self.journal.open('ab') as f:f.write((json.dumps(m,ensure_ascii=False,sort_keys=True)+'\n').encode());f.flush()
        if m.get('http_status')==200:self.cache[m['request_key']]=m
    def cached(self,method,url,params):
        k=key(method,url,params)
        if k in self.memory:return self.memory[k]
        m=self.cache.get(k)
        if m:
            raw=gzip.decompress((STATE/m['raw_file']).read_bytes())
            if sha(raw)!=m['sha256']:raise Stop('cached_sha_mismatch')
            if url==PROFILE:self.memory[k]=(raw,m)
            return raw,m
        if k in self.external:
            old,path=self.external[k]
            if path.exists():
                raw=path.read_bytes()
                if sha(raw)!=old['sha256']:raise Stop('inherited_sha_mismatch')
                rec={'request_key':k,'origin':'reused_cache','method':method,'url':url,'params':params,'http_status':200,'received_at':old['received_at'],'source_cache':str(path)}
                self.append(rec,raw)
                if url==PROFILE:self.memory[k]=(raw,rec)
                return raw,rec
        return None
    def fetch(self,method,url,params,allow_network=True):
        cached=self.cached(method,url,params)
        if cached:return cached
        if not allow_network:return None
        k=key(method,url,params)
        for attempt in range(2):
            if self.count>=MAX:raise Stop('request_cap')
            wait=max(0,DELAY-(time.monotonic()-self.last))
            if wait:time.sleep(wait)
            self.count+=1;self.update(request_key=k,request_in_progress=True)
            rec={'request_key':k,'origin':'new_request','method':method,'url':url,'params':params,'attempt':attempt+1}
            try:
                r=self.s.request(method,url,params=params if method=='GET' else None,data=params if method=='POST' else None,timeout=(5,15),allow_redirects=False)
                raw=r.content;rec.update(http_status=r.status_code,received_at=datetime.now(timezone.utc).isoformat());self.append(rec,raw)
            except requests.RequestException as e:
                rec.update(http_status=None,failed_at=datetime.now(timezone.utc).isoformat(),error_type=type(e).__name__);self.append(rec);r=None
            finally:self.last=time.monotonic();self.update(request_in_progress=False)
            if r is not None and r.status_code==200:
                if url==PROFILE:self.memory[k]=(raw,rec)
                return raw,rec
            if r is not None and r.status_code==403:raise Stop('http_403')
            if r is not None and r.status_code!=429:raise Stop('http_'+str(r.status_code))
            self.update(backoff_seconds=15 if attempt==0 else 0)
            if attempt==0:time.sleep(15)
        raise Stop('network_or_429_after_backoff')

def stage1(row,c):
    fid=row['fixture_id'];direct=bool(re.fullmatch(r'\d{8}[A-Z]{4}\d\d{4}',fid));sm=re.fullmatch(r'score:(\d{4})-(\d{2})-(\d{2}):([^:]+):([^:]+)',fid)
    sentinel=direct and not fid.startswith('2025')
    if direct:
        gid=fid[:-4]
        if sentinel:
            if not row.get('kickoff_at'):raise ValueError('sentinel_fixture_missing_date')
            day=datetime.fromisoformat(row['kickoff_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d')
            if fid[4:8]!=day[4:8]:raise ValueError('sentinel_date_encoding_mismatch')
        else:day=fid[:8]
    elif sm and (sm.group(4),sm.group(5))==(row['home_id'],row['away_id']):gid=None;day=''.join(sm.group(i) for i in (1,2,3))
    else:raise ValueError('invalid_fixture_id')
    if not day.startswith('2025'):raise ValueError('wrong_season')
    raw,smeta=c.fetch('POST',SCHEDULE,{'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day});s=json.loads(raw)
    if str(s.get('code'))!='100' or not isinstance(s.get('game'),list):raise ValueError('invalid_schedule')
    basis='exact_official_game_id' if direct else 'unique_date_home_away_schedule_match'
    if sentinel:
        exact=[g for g in s['game'] if g.get('G_ID')==gid]
        if len(exact)==1:g=exact[0]
        elif not exact:
            matches=[g for g in s['game'] if (g.get('HOME_NM','').strip(),g.get('AWAY_NM','').strip(),str(g.get('HEADER_NO')))==(row['home_id'],row['away_id'],fid[-5])]
            if len(matches)!=1:raise ValueError('sentinel_fixture_schedule_not_unique')
            g=matches[0]
            if g['G_ID'][4:]!=fid[4:13]:raise ValueError('sentinel_game_id_alias_mismatch')
            basis='encoded_year_alias_exact_official_schedule_match'
        else:raise ValueError('sentinel_official_game_id_not_unique')
    else:g=select_schedule_game(s['game'],gid,row['home_id'],row['away_id'])
    gid=g['G_ID']
    if str(g['G_DT'])!=day or str(g['SEASON_ID'])!='2025' or str(g['LE_ID'])!='1' or not gid.endswith(g['AWAY_ID']+g['HOME_ID']+str(g['HEADER_NO'])) or (not sentinel and gid!=day+g['AWAY_ID']+g['HOME_ID']+str(g['HEADER_NO'])):raise ValueError('official_game_identity_mismatch')
    if (g['HOME_NM'].strip(),g['AWAY_NM'].strip())!=(row['home_id'],row['away_id']):raise ValueError('team_mismatch')
    if str(g.get('GAME_STATE_SC'))!='3' or str(g.get('GAME_RESULT_CK'))!='1':raise ValueError('game_not_confirmed_finished')
    kickoff_match=None
    if row.get('kickoff_at'):
        kickoff_match=datetime.fromisoformat(row['kickoff_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d %H:%M')==day+' '+g['G_TM']
        # Exact official game ID includes the doubleheader number and is a
        # stronger identity than an archived, potentially rescheduled time.
        if not kickoff_match and basis!='exact_official_game_id':raise ValueError('kickoff_mismatch_without_exact_game_id')
    params={'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':gid}
    raw,lmeta=c.fetch('POST',LINEUP,params);lineup=parse_lineup(raw,g)
    return {'fixture_id':fid,'season':2025,'date':day,'official_game_id':gid,'home_team':{'id':g['HOME_ID'],'name':g['HOME_NM'].strip()},'away_team':{'id':g['AWAY_ID'],'name':g['AWAY_NM'].strip()},'archived_kickoff_at':row.get('kickoff_at'),'source_kickoff_matches_official':kickoff_match,'fixture_match_basis':basis,'schedule_game':{k:g.get(k) for k in ('G_ID','G_DT','G_TM','HOME_ID','AWAY_ID','SEASON_ID','LE_ID','SR_ID','HEADER_NO','B_PIT_P_ID','B_PIT_P_NM','T_PIT_P_ID','T_PIT_P_NM')},'lineup_flag':lineup is not None,'lineups':lineup,'schedule_evidence':smeta,'lineup_evidence':lmeta,'exclusion_reasons':[] if lineup else ['lineup_flag_false']}

def starter_from_box(box,g,side):
    table=box['tables'][4 if side=='home' else 3];heads=[x['Text'].strip() for x in table['headers'][0]['row']];role=heads.index('등판');names=heads.index('선수명')
    rows=[[str(c.get('Text') or '').strip() for c in x['row']] for x in table['rows']];starts=[r for r in rows if len(r)>max(role,names) and r[role]=='선발']
    if len(starts)!=1:raise ValueError('actual_starter_not_unique')
    value=starts[0][names];prefix='B' if side=='home' else 'T';pid=str(g.get(prefix+'_PIT_P_ID') or '');name=str(g.get(prefix+'_PIT_P_NM') or '').strip()
    if not pid.isdigit() or not name or value not in (pid,name):raise ValueError('actual_starter_schedule_record_mismatch')
    return {'player_id':pid,'name':name,'actual_record_verified':True,'actual_record_starter_cell':value,'identity_basis':'official_same_game_starter_marker_and_matching_schedule_identity'}

def main():
    STATE.mkdir(parents=True,exist_ok=True);raw=ROWS.read_bytes()
    if sha(raw)!=EXPECTED or sha(BATTERS.read_bytes())!=BATTER_SHA:raise ValueError('fixed_input_sha_mismatch')
    rows=[x for x in map(json.loads,raw.splitlines()) if x['season']==2025]
    if len(rows)!=736 or len({x['fixture_id'] for x in rows})!=736:raise ValueError('2025_denominator_mismatch')
    c=Capture({'denominator':736,'phase':'lineups'});stop='complete'
    try:
        for index,row in enumerate(rows):
            path=fidpath('stage1',row['fixture_id']);c.update(phase='lineups',fixture_index=index+1,completed=len(list((STATE/'stage1').glob('*.json'))) if (STATE/'stage1').exists() else 0)
            if path.exists():
                old=json.loads(path.read_bytes())
                if old.get('exclusion_reasons') not in (['kickoff_mismatch'],['wrong_season']):continue
            try:record=stage1(row,c)
            except (ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError) as e:record={'fixture_id':row['fixture_id'],'season':2025,'lineup_flag':False,'lineups':None,'exclusion_reasons':[str(e)[:180]]}
            write(path,record)
        for index,row in enumerate(rows):
            fid=row['fixture_id'];path=fidpath('stage2',fid);c.update(phase='actual_starters',fixture_index=index+1,completed=len(list((STATE/'stage2').glob('*.json'))) if (STATE/'stage2').exists() else 0)
            if path.exists():
                previous=json.loads(path.read_bytes());first_path=fidpath('stage1',fid)
                if previous.get('exclusion_reasons')!=['stage1_unresolved'] or not first_path.exists() or 'schedule_game' not in json.loads(first_path.read_bytes()):continue
            first=json.loads(fidpath('stage1',fid).read_bytes())
            if 'schedule_game' not in first:write(path,{'fixture_id':fid,'exclusion_reasons':['stage1_unresolved']});continue
            g=first['schedule_game'];params={'leId':g['LE_ID'],'srId':g['SR_ID'],'seasonId':g['SEASON_ID'],'gameId':g['G_ID']}
            try:
                b,meta=c.fetch('POST',BOX,params);box=json.loads(b)
                if str(box.get('code'))!='100' or len(box.get('tables') or [])!=5:raise ValueError('invalid_box_score')
                starters={};errors=[]
                for side in ('home','away'):
                    try:starters[side]=starter_from_box(box,g,side)
                    except (ValueError,KeyError,IndexError,TypeError) as e:starters[side]=None;errors.append(side+':'+str(e))
                record={'fixture_id':fid,'starters':starters,'box_evidence':meta,'exclusion_reasons':errors}
            except (ValueError,KeyError,TypeError,IndexError,json.JSONDecodeError) as e:record={'fixture_id':fid,'exclusion_reasons':[str(e)[:180]]}
            write(path,record)
    except Stop as e:stop=str(e)
    c.update(phase='stopped',stop_reason=stop,stage1_count=len(list((STATE/'stage1').glob('*.json'))) if (STATE/'stage1').exists() else 0,stage2_count=len(list((STATE/'stage2').glob('*.json'))) if (STATE/'stage2').exists() else 0)
    print(json.dumps(c.progress,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
