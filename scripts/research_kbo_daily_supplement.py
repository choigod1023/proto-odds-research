"""KBO public daily batter statistics supplement; no production writes or cookies."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime,timezone
import gzip,hashlib,json,re,time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT='https://www.koreabaseball.com'
DAILY=ROOT+'/Record/Player/HitterDetail/Daily.aspx'
PITCHER=ROOT+'/Record/Player/PitcherDetail/Basic.aspx'
V1_SHA='e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334'
LINEUP_SHA='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3'

def sha(b):return hashlib.sha256(b).hexdigest()
def soup(raw):return BeautifulSoup(raw.decode('utf8'),'html.parser')
def profile_name(doc):
    nodes=doc.find_all(id=lambda v:v and v.endswith('playerProfile_lblName'))
    if len(nodes)!=1:return None
    return nodes[0].get_text(' ',strip=True)

def form_state(doc,series):
    form=doc.select_one('form#mainForm');y=doc.select_one('select[id$=ddlYear]');s=doc.select_one('select[id$=ddlSeries]')
    if not form or not y or not s or not y.get('name') or not s.get('name'):raise ValueError('missing_public_form')
    if not y.select_one('option[value="2026"]') or not s.select_one(f'option[value="{series}"]'):raise ValueError('season_or_series_unavailable')
    fields={e['name']:e.get('value','') for e in form.select('input[type=hidden][name]')}
    fields['__EVENTTARGET']=s['name'];fields['__EVENTARGUMENT']='';fields[y['name']]='2026';fields[s['name']]=str(series)
    return fields,{'event_target':s['name'],'year_field':y['name'],'series_field':s['name'],'year':'2026','series':str(series)}

def selected(doc):
    vals=[]
    for name in ('ddlYear','ddlSeries'):
        node=doc.select_one(f'select[id$={name}] option[selected]')
        vals.append(node.get('value') if node else None)
    return tuple(vals)

def int_or_error(v):
    if not re.fullmatch(r'\d+',str(v)):raise ValueError('invalid_nonnegative_integer')
    return int(v)

def daily_rows(doc,series):
    if selected(doc)!=('2026',str(series)):raise ValueError('season_series_mismatch')
    out=[]
    for table in doc.find_all('table'):
        heads=[c.get_text(' ',strip=True) for c in table.select('thead th')]
        required=('PA','AB','H','R','RBI','BB','HBP','HR','SO','2B','3B')
        if not all(heads.count(x)==1 for x in required):continue
        for tr in table.select('tbody tr'):
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
            if len(cells)!=len(heads):continue
            if not re.fullmatch(r'\d{2}\.\d{2}',cells[0]):continue
            fields={key:int_or_error(cells[heads.index(key)]) for key in required}
            if fields['H']>fields['AB'] or fields['SO']>fields['AB'] or fields['2B']+fields['3B']+fields['HR']>fields['H'] or fields['AB']+fields['BB']+fields['HBP']>fields['PA']:
                raise ValueError('invalid_batter_stat_relationship')
            out.append({'date':'2026'+cells[0].replace('.',''),'opponent':cells[1],**fields})
    return out

def unique_game(schedule,game):
    if str(schedule.get('code'))!='100' or not isinstance(schedule.get('game'),list):return False
    teams={game['team_ids']['home'],game['team_ids']['away']}
    matches=[x for x in schedule['game'] if x.get('G_DT')==game['game_date']
             and {x.get('HOME_ID'),x.get('AWAY_ID')}==teams]
    return len(matches)==1 and matches[0].get('G_ID')==game['official_game_id']

def merge_batter(game,side,player,daily,opponent_name,schedule):
    base={'name':player['name'],'player_id':player['player_id'],'side':side,'batting_order':player['batting_order'],
          'official_v1':{k:player[k] for k in ('AB','H','R','RBI')},'source':'KBO official Daily.aspx',
          'BB':None,'HR':None,'K':None,'2B':None,'3B':None,'HBP':None}
    if not player['player_id']:return {**base,'status':'missing_player_id'}
    if not unique_game(schedule,game):return {**base,'status':'ambiguous_or_missing_game'}
    candidates=[r for r in daily if r['date']==game['game_date'] and r['opponent']==opponent_name]
    if len(candidates)!=1:return {**base,'status':'daily_row_not_unique','candidate_count':len(candidates)}
    row=candidates[0];base['daily_official_row']=row
    conflict={k:{'boxscore':player[k],'daily':row[k]} for k in ('AB','H','R','RBI') if player[k]!=row[k]}
    if conflict:return {**base,'status':'conflict','conflict':conflict}
    return {**base,'status':'matched','BB':row['BB'],'HR':row['HR'],'K':row['SO'],'2B':row['2B'],'3B':row['3B'],'HBP':row['HBP'],
            'field_basis':'official_daily_game_row_matched_game_team_date_and_boxscore'}


def reconcile_team(players,pitchers):
    if not players or not pitchers or not all(x['status']=='matched' for x in players) or not all(all(p[key] is not None for key in ('BB_plus_HBP','HR','K')) for p in pitchers):
        return {'status':'not_checkable_partial'}
    daily={'BB_plus_HBP':sum(x['BB']+x['HBP'] for x in players),'HR':sum(x['HR'] for x in players),'K':sum(x['K'] for x in players)}
    official={key:sum(p[key] for p in pitchers) for key in ('BB_plus_HBP','HR','K')}
    consistent=daily==official
    if not consistent:
        for x in players:
            x['status']='team_total_conflict'
            for key in ('BB','HR','K','2B','3B','HBP'):x[key]=None
    return {'status':'matched' if consistent else 'conflict','daily':daily,'opposing_pitchers':official}

@contextmanager
def lock(path):
    path.mkdir(parents=True,exist_ok=True);f=(path/'capture.lock').open('a+b')
    try:
        f.seek(0,2)
        if not f.tell():f.write(b'0');f.flush()
        f.seek(0)
        try:
            if __import__('os').name=='nt':
                import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:raise RuntimeError('collector_already_running') from e
        yield
    finally:f.close()

class Stop(Exception):pass

class Fetcher:
    def __init__(self,root,limit,offline=False):
        self.root=root;self.limit=limit;self.offline=offline;self.n=0;self.last=0
        (root/'raw').mkdir(parents=True,exist_ok=True);(root/'requests').mkdir(parents=True,exist_ok=True)
    def request(self,method,url,query,tag,form=None,public_form=None):
        key=sha(json.dumps([method,url,query,tag],sort_keys=True).encode());file=self.root/'requests'/f'{key}.json'
        if file.exists():
            m=json.loads(file.read_text(encoding='utf8'))
            if m.get('http_status')!=200:raise Stop('cached_failure')
            b=gzip.decompress((self.root/'raw'/f"{m['sha256']}.gz").read_bytes())
            if sha(b)!=m['sha256']:raise Stop('cache_hash_mismatch')
            return b,m
        if self.offline:raise Stop('cache_miss_offline')
        if self.n>=self.limit:raise Stop('request_limit')
        time.sleep(max(0,1-(time.monotonic()-self.last)));self.n+=1;start=datetime.now(timezone.utc).isoformat()
        try:
            # Top-level request creates no persistent cookie session.
            r=requests.request(method,url,params=query,data=form,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity','Referer':url},timeout=(5,15),allow_redirects=False)
        except requests.RequestException as e:
            meta={'url':url,'method':method,'query':query,'tag':tag,'started_at':start,'failed_at':datetime.now(timezone.utc).isoformat(),'http_status':None,'failure':type(e).__name__}
            file.write_text(json.dumps(meta,sort_keys=True)+'\n',encoding='utf8');raise Stop('network:'+type(e).__name__) from e
        finally:self.last=time.monotonic()
        b=r.content;meta={'url':url,'method':method,'query':query,'tag':tag,'http_status':r.status_code,'received_at':datetime.now(timezone.utc).isoformat(),'sha256':sha(b),'bytes':len(b),'content_encoding':r.headers.get('Content-Encoding'),'form':public_form}
        (self.root/'raw'/f"{meta['sha256']}.gz").write_bytes(gzip.compress(b,mtime=0));file.write_text(json.dumps(meta,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
        if r.status_code!=200:raise Stop('http:'+str(r.status_code))
        return b,meta

def load_inputs(v1,lineups,raw_dir):
    v=Path(v1).read_bytes();l=Path(lineups).read_bytes()
    if sha(v)!=V1_SHA or sha(l)!=LINEUP_SHA:raise ValueError('frozen_source_sha_mismatch')
    games=[json.loads(x) for x in gzip.decompress(v).splitlines()];source={x['official_game_id']:x for x in map(json.loads,l.splitlines())}
    if len(games)!=256 or len({g['official_game_id'] for g in games})!=256:raise ValueError('source_denominator')
    schedules={}
    for game in games:
        original=source[game['official_game_id']];ev=original['schedule_evidence'];body=(Path(raw_dir)/ev['sha256']).read_bytes()
        if sha(body)!=ev['sha256']:raise ValueError('schedule_sha_mismatch')
        schedules[game['official_game_id']]=json.loads(body)
    return games,schedules

def run(args):
    games,schedules=load_inputs(args.v1,args.lineups,args.lineup_raw);out=Path(args.output)
    if (out/args.snapshot).exists() or (out/(args.snapshot+'.manifest.json')).exists():raise FileExistsError('snapshot_exists')
    f=Fetcher(out,args.max_requests,args.offline)
    pnames=defaultdict(set);tasks=set()
    for g in games:
        for side in ('home','away'):
            for p in g['statistics']['pitchers'][side]:
                if not p['player_id'] and p['source_numeric_id_claim']:pnames[p['source_numeric_id_claim']].add(p['name'])
            for p in g['statistics']['batters'][side]:
                if p['player_id']:tasks.add((g['series_id'],p['player_id']))
    cache={};profiles={};stop='complete'
    try:
        for pid in sorted(pnames):
            if len(pnames[pid])!=1:continue
            raw,meta=f.request('GET',PITCHER,{'playerId':pid},'pitcher-profile')
            name=profile_name(soup(raw));profiles[pid]={'name':name,'evidence':meta}
        for series,pid in sorted(tasks,key=lambda x:(x[0]!=0,x[0],x[1])):
            raw,get=f.request('GET',DAILY,{'playerId':pid},'daily-get')
            meta=get
            try:
                doc=soup(raw)
                if selected(doc)==('2026',str(series)):body=raw;meta=get
                else:
                    form,desc=form_state(doc,series);desc['public_form_state_source_sha256']=sha(raw)
                    body,meta=f.request('POST',DAILY,{'playerId':pid},'daily-series-'+str(series),form=form,public_form=desc)
                parsed=soup(body);name=profile_name(parsed);cache[(series,pid)]={'name':name,'rows':daily_rows(parsed,series),'evidence':meta}
            except (ValueError,UnicodeError) as exc:
                cache[(series,pid)]={'error':str(exc),'evidence':meta}
    except Stop as e:stop=str(e)
    # Inspect all previously cached tasks without making further requests, so
    # partial captures and pre-existing samples are usable after any stop.
    f.offline=True
    for pid in sorted(pnames):
        if pid in profiles or len(pnames[pid])!=1:continue
        try:
            raw,meta=f.request('GET',PITCHER,{'playerId':pid},'pitcher-profile')
            profiles[pid]={'name':profile_name(soup(raw)),'evidence':meta}
        except (Stop,ValueError,UnicodeError):continue
    for series,pid in sorted(tasks,key=lambda x:(x[0]!=0,x[0],x[1])):
        if (series,pid) in cache:continue
        try:
            raw,get=f.request('GET',DAILY,{'playerId':pid},'daily-get');doc=soup(raw)
            if selected(doc)==('2026',str(series)):body,meta=raw,get
            else:
                form,desc=form_state(doc,series);desc['public_form_state_source_sha256']=sha(raw)
                body,meta=f.request('POST',DAILY,{'playerId':pid},'daily-series-'+str(series),form=form,public_form=desc)
            parsed=soup(body);cache[(series,pid)]={'name':profile_name(parsed),'rows':daily_rows(parsed,series),'evidence':meta}
        except (Stop,ValueError,UnicodeError):continue
    records=[];counts=defaultdict(int)
    for g in games:
        schedule=schedules[g['official_game_id']];official=[x for x in schedule.get('game',[]) if x.get('G_ID')==g['official_game_id']]
        target=official[0] if len(official)==1 else {}
        record={'official_game_id':g['official_game_id'],'fixture_id':g['fixture_id'],'game_date':g['game_date'],
                'series_id':g['series_id'],'historical_receipt_proven':False,'target_game_outcome_not_feature':True,
                'source_v1_gzip_sha256':V1_SHA,
                'causality':'retrospective_completed_game_date_strictly_before_target','batters':{},'pitcher_id_supplement':{}}
        for side,oppo in (('home','AWAY_NM'),('away','HOME_NM')):
            outp=[]
            for row_index,p in enumerate(g['statistics']['batters'][side]):
                entry=cache.get((g['series_id'],p['player_id'])) if p['player_id'] else None
                if entry and entry.get('error'):
                    row={'name':p['name'],'player_id':p['player_id'],'status':'daily_parse_error','reason':entry['error'],'daily_evidence':entry['evidence'],'BB':None,'HR':None,'K':None,'2B':None,'3B':None,'HBP':None}
                elif entry and entry['name']==p['name']:
                    row=merge_batter(g,side,p,entry['rows'],str(target.get(oppo,'')).strip(),schedule);row['daily_evidence']=entry['evidence']
                elif entry:
                    row={'name':p['name'],'player_id':p['player_id'],'status':'profile_name_conflict','daily_profile_name':entry['name'],'daily_evidence':entry['evidence'],'BB':None,'HR':None,'K':None,'2B':None,'3B':None,'HBP':None}
                else:row={'name':p['name'],'player_id':p['player_id'],'status':'missing_player_id' if not p['player_id'] else 'daily_not_captured','BB':None,'HR':None,'K':None,'2B':None,'3B':None,'HBP':None}
                row['v1_row_index']=row_index;row['official_v1']={key:p[key] for key in ('AB','H','R','RBI')}
                counts['batter_'+row['status']]+=1;outp.append(row)
            record['batters'][side]=outp
            outp=[]
            for row_index,p in enumerate(g['statistics']['pitchers'][side]):
                claim=p.get('source_numeric_id_claim');profile=profiles.get(claim) if claim and not p['player_id'] else None
                ok=profile and len(pnames[claim])==1 and profile['name']==p['name']
                status='verified_official_profile' if ok else 'profile_unavailable_or_conflicting' if claim and not p['player_id'] else 'already_linked' if p['player_id'] else 'no_official_numeric_claim'
                item={'name':p['name'],'v1_row_index':row_index,'original_player_id':p['player_id'],'source_numeric_id_claim':claim,
                      'supplement_player_id':claim if ok else None,'status':status,'profile_evidence':profile['evidence'] if profile else None}
                counts['pitcher_'+status]+=1;outp.append(item)
            record['pitcher_id_supplement'][side]=outp
        record['team_reconciliation']={}
        for side,opponent in (('home','away'),('away','home')):
            players=record['batters'][side];pitchers=g['statistics']['pitchers'][opponent]
            record['team_reconciliation'][side]=reconcile_team(players,pitchers)
        records.append(record)
    counts=defaultdict(int)
    for record in records:
        for side in ('home','away'):
            for row in record['batters'][side]:counts['batter_'+row['status']]+=1
            for row in record['pitcher_id_supplement'][side]:counts['pitcher_'+row['status']]+=1
    payload=b''.join((json.dumps(x,sort_keys=True,ensure_ascii=False)+'\n').encode() for x in records)
    snapshot=out/args.snapshot
    with snapshot.open('xb') as dest:dest.write(gzip.compress(payload,mtime=0))
    manifest={'games':len(games),'batter_tasks':len(tasks),'batter_tasks_captured':len(cache),'pitcher_candidate_ids':len(pnames),
              'pitcher_profiles_captured':len(profiles),'counts':dict(counts),'new_requests':f.n,'stop_reason':stop,'selection':'pitcher candidate IDs ascending, then KBO regular-season player IDs ascending, then other series ascending',
              'historical_receipt_proven':False,'source_v1_gzip_sha256':V1_SHA,'source_lineups_sha256':LINEUP_SHA,
              'supplement_uncompressed_sha256':sha(payload),'supplement_gzip_sha256':sha(snapshot.read_bytes())}
    print(json.dumps(manifest,ensure_ascii=False));(out/(args.snapshot+'.manifest.json')).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--v1',required=True);p.add_argument('--lineups',required=True);p.add_argument('--lineup-raw',required=True);p.add_argument('--output',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--max-requests',type=int,default=290);p.add_argument('--offline',action='store_true');a=p.parse_args()
    if not 1<=a.max_requests<=290:p.error('max-requests must be between 1 and 290')
    if Path(a.snapshot).name!=a.snapshot:p.error('snapshot must be a filename')
    with lock(Path(a.output)):run(a)
