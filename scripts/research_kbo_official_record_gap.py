"""Detached KBO postgame game-stat supplement; never writes production data."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import re
import time
from pathlib import Path
import requests

ROOT='https://www.koreabaseball.com'
BOX=ROOT+'/ws/Schedule.asmx/GetBoxScore'
SCROLL=ROOT+'/ws/Schedule.asmx/GetBoxScoreScroll'
SOURCE_SHA='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3'


def sha(b): return hashlib.sha256(b).hexdigest()

def number(value):
    value=str(value).strip()
    if value in ('','-','&nbsp;'): return None
    if not re.fullmatch(r'\d+',value): raise ValueError('invalid_nonnegative_integer')
    return int(value)

def innings_outs(value):
    value=str(value).strip()
    if value in ('','-','&nbsp;'): return None
    if re.fullmatch(r'[012]/3',value): return int(value[0])
    m=re.fullmatch(r'(\d+)(?:\s+([012])/3|\.([012]))?',value)
    if not m: raise ValueError('invalid_innings')
    return 3*int(m.group(1))+int(m.group(2) or m.group(3) or 0)

def text(row): return [str(x.get('Text') or '').strip() for x in row['row']]

def header(table):
    rows=table.get('headers') or []
    if len(rows)!=1: raise ValueError('missing_headers')
    return text(rows[0])

def table_json(value): return json.loads(value) if isinstance(value,str) else value

def hitter_table(table, game, side, index):
    head=header(table)
    required=('선수명','타수','안타','타점','득점','타율')
    if any(head.count(x)!=1 for x in required) or head.index('선수명')!=2: raise ValueError('hitter_headers')
    announced={p['order']:p['name'] for p in game['lineups'][side]}
    result=[]; matched=set()
    for row in table.get('rows',[]):
        cells=text(row)
        if len(cells)!=len(head): raise ValueError('hitter_row_width')
        order=number(cells[0]);name=cells[2];pos=cells[1]
        if order not in range(1,10) or not name or not pos: raise ValueError('hitter_identity')
        if announced.get(order)==name: matched.add(order)
        candidates=index.get((game[side+'_team_id'],name),set())
        pid=next(iter(candidates)) if len(candidates)==1 else None
        result.append({'name':name,'player_id':pid,'player_id_basis':'postgame_prior_official_lineup_profile_team_name' if pid else None,
                       'batting_order':order,'position':pos,
                       'announced_lineup_match':announced.get(order)==name,
                       'AB':number(cells[head.index('타수')]),'H':number(cells[head.index('안타')]),
                       'RBI':number(cells[head.index('타점')]),'R':number(cells[head.index('득점')]),
                       'BB':None,'HR':None,'K':None,'2B':None,'3B':None})
    if len(matched)<6: raise ValueError('insufficient_official_lineup_identity_match')
    for key in ('AB','H','RBI','R'):
        col={'AB':'타수','H':'안타','RBI':'타점','R':'득점'}[key]; foot=table.get('tfoot') or []
        if len(foot)==1:
            f=text(foot[0]); expected=number(f[-5+('AB','H','RBI','R').index(key)]) if len(f)>=5 else None
            vals=[p[key] for p in result]
            if expected is not None and all(v is not None for v in vals) and sum(vals)!=expected: raise ValueError('hitter_total_mismatch:'+col)
    return result

def pitcher_table(table, scroll_table, game, side, pitcher_index, archive_index=None):
    hs=header(table); ht=header(scroll_table)
    needed=('선수명','등판','결과','이닝','타자','투구수','타수','피안타','홈런','4사구','삼진','실점','자책')
    if hs!=ht or any(hs.count(x)!=1 for x in needed): raise ValueError('pitcher_headers')
    a=table.get('rows') or [];b=scroll_table.get('rows') or []
    if len(a)!=len(b) or not a: raise ValueError('pitcher_row_count')
    players=[]
    for left,right in zip(a,b):
        c=text(left);s=text(right)
        if len(c)!=len(hs) or len(s)!=len(hs) or c[1:]!=s[1:]: raise ValueError('pitcher_table_crosscheck')
        name=s[0]; direct=c[0] if c[0].isdigit() else None
        if not name or name.isdigit(): raise ValueError('pitcher_name_missing')
        if not direct and c[0]!=name: raise ValueError('pitcher_name_conflict')
        candidates=pitcher_index.get((game[side+'_team_id'],name),set())
        archived=(archive_index or {}).get((game[side+'_team_id'],name),set())
        # A numeric field in one official table is retained as a claim, but only
        # an exact team/name/ID crossmatch permits a normalized player ID.
        pid=direct if direct and direct in candidates and len(candidates)==1 else (next(iter(candidates)) if not direct and len(candidates)==1 else None)
        basis='official_schedule_name_and_team_crossmatch' if pid else None
        if pid is None and direct and len(archived)==1 and direct in archived and (not candidates or candidates==archived):
            pid=direct;basis='official_numeric_and_prior_archive_team_name_crossmatch'
        def get(label): return number(s[hs.index(label)])
        players.append({'name':name,'player_id':pid,'source_numeric_id_claim':direct,
            'player_id_basis':basis,
            'is_starter':s[hs.index('등판')]=='선발','appearance':s[hs.index('등판')],
            'result_label':s[hs.index('결과')], 'outs':innings_outs(s[hs.index('이닝')]),
            'batters_faced':get('타자'),'pitch_count':get('투구수'),'H':get('피안타'),
            'HR':get('홈런'),'BB':None,'BB_plus_HBP':get('4사구'),'K':get('삼진'),
            'R':get('실점'),'ER':get('자책')})
    if sum(p['is_starter'] for p in players)!=1: raise ValueError('pitcher_starter_not_unique')
    quality={'team_earned_runs':None,'individual_earned_runs_sum':None,'team_equals_individual_earned_runs':None}
    foot=scroll_table.get('tfoot') or []
    if len(foot)==1:
        totals=text(foot[0]);team_outs=innings_outs(totals[1]) if len(totals)>1 else None
        player_outs=[p['outs'] for p in players]
        quality.update({'team_outs':team_outs,
                        'individual_outs_sum':sum(player_outs) if all(v is not None for v in player_outs) else None,
                        'team_equals_individual_outs':team_outs==sum(player_outs) if team_outs is not None and all(v is not None for v in player_outs) else None})
    for h,key in [('타자','batters_faced'),('투구수','pitch_count'),('피안타','H'),('홈런','HR'),('4사구','BB_plus_HBP'),('삼진','K'),('실점','R'),('자책','ER')]:
        foot=scroll_table.get('tfoot') or []
        if len(foot)==1:
            row=text(foot[0]); idx=hs.index(h)-5
            if 0<=idx<len(row):
                target=number(row[idx]);vals=[p[key] for p in players]
                if h=='자책' and target is not None and all(v is not None for v in vals):
                    quality.update({'team_earned_runs':target,'individual_earned_runs_sum':sum(vals),
                                    'team_equals_individual_earned_runs':target==sum(vals)})
                elif target is not None and all(v is not None for v in vals) and sum(vals)!=target:
                    raise ValueError('pitcher_total_mismatch:'+h)
    return players,quality

def prior_only(records,target_date):
    if not re.fullmatch(r'\d{8}',target_date): raise ValueError('target_date')
    try: datetime.strptime(target_date,'%Y%m%d')
    except ValueError as exc: raise ValueError('target_date') from exc
    # Do not pass the outcome namespace to downstream feature code.
    return [r['statistics'] for r in records if r.get('game_date','') < target_date and r.get('completed') is True and r.get('status')=='ok']

@contextmanager
def capture_lock(path):
    path.mkdir(parents=True,exist_ok=True);f=(path/'capture.lock').open('a+b')
    try:
        f.seek(0,2)
        if not f.tell(): f.write(b'0');f.flush()
        f.seek(0)
        try:
            if __import__('os').name=='nt':
                import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc: raise RuntimeError('collector_already_running') from exc
        yield
    finally: f.close()

class Stop(Exception):pass

class Fetcher:
    def __init__(self,path,limit,offline=False):
        self.path=path;self.limit=limit;self.offline=offline;self.n=0;self.last=0
        (path/'requests').mkdir(parents=True,exist_ok=True);(path/'raw').mkdir(parents=True,exist_ok=True)
        self.s=requests.Session();self.s.headers.update({'User-Agent':'Mozilla/5.0','Referer':ROOT+'/Schedule/GameCenter/Main.aspx','Accept-Encoding':'identity'})
    def post(self,url,params):
        key=sha(json.dumps([url,params],sort_keys=True).encode());meta_path=self.path/'requests'/f'{key}.json'
        if meta_path.exists():
            m=json.loads(meta_path.read_text(encoding='utf8'));raw=gzip.decompress((self.path/'raw'/f"{m['sha256']}.gz").read_bytes())
            if sha(raw)!=m['sha256'] or m['http_status']!=200: raise Stop('cached_http_or_sha_failure')
            return raw,m
        if self.offline: raise Stop('cache_miss_offline')
        if self.n>=self.limit: raise Stop('request_limit')
        time.sleep(max(0,1-(time.monotonic()-self.last)));self.n+=1
        try:r=self.s.post(url,data=params,timeout=(5,15),allow_redirects=False);raw=r.content
        except requests.RequestException as exc:raise Stop('network:'+type(exc).__name__) from exc
        finally:self.last=time.monotonic()
        m={'url':url,'method':'POST','params':params,'http_status':r.status_code,'received_at':datetime.now(timezone.utc).isoformat(),
           'sha256':sha(raw),'bytes':len(raw),'content_type':r.headers.get('Content-Type'), 'content_encoding':r.headers.get('Content-Encoding')}
        (self.path/'raw'/f"{m['sha256']}.gz").write_bytes(gzip.compress(raw,mtime=0));meta_path.write_text(json.dumps(m,sort_keys=True)+'\n',encoding='utf8')
        if r.status_code!=200:raise Stop('http:'+str(r.status_code))
        return raw,m

def indexes(games):
    hitters={};pitchers={}
    for g in games:
        for side in ('home','away'):
            team=g[side+'_team_id']
            for p in (g.get('lineups') or {}).get(side,[]):
                if p.get('player_id') and p.get('official_profile_evidence'):
                    hitters.setdefault((team,p['name']),set()).add(str(p['player_id']))
            p=(g.get('starting_pitchers') or {}).get(side) or {}
            if p.get('player_id') and p.get('name'):
                pitchers.setdefault((team,p['name'].strip()),set()).add(str(p['player_id']))
    return hitters,pitchers

def archived_pitcher_index(games, lineup_raw, archive):
    """Use pre-window player names only; never import the archived stats."""
    team_names={}
    for game in games:
        evidence=game['schedule_evidence'];raw=(Path(lineup_raw)/evidence['sha256']).read_bytes()
        if sha(raw)!=evidence['sha256']:raise ValueError('source_schedule_sha')
        match=[x for x in json.loads(raw)['game'] if x['G_ID']==game['official_game_id']]
        if len(match)!=1:raise ValueError('source_schedule_identity')
        for side in ('HOME','AWAY'):
            team_names.setdefault(match[0][side+'_NM'].strip(),set()).add(match[0][side+'_ID'])
    result={}
    for fid,game in archive.items():
        date=game.get('date')
        if not fid.endswith('2026') or not isinstance(date,str) or not re.fullmatch(r'2026-\d{2}-\d{2}',date) or date>'2026-07-26':continue
        for side in ('home','away'):
            teams=team_names.get(str(game.get(side,'')).strip(),set())
            if len(teams)!=1:continue
            for player in (game.get('data') or {}).get(side,[]):
                name=str(player.get('name') or '').strip();pid=str(player.get('pcode') or '')
                if name and pid.isdigit():result.setdefault((next(iter(teams)),name),set()).add(pid)
    return result


def normalize(game, schedule, box_raw, scroll_raw, hitter_index, pitcher_index, archive_index=None):
    b=json.loads(box_raw);sc=json.loads(scroll_raw); tables=b.get('tables')
    if str(b.get('code'))!='100' or str(sc.get('code'))!='100' or not isinstance(tables,list) or len(tables)!=5:raise ValueError('invalid_box_envelope')
    if str(schedule.get('GAME_STATE_SC'))!='3' or str(schedule.get('GAME_RESULT_CK'))!='1':raise ValueError('game_not_confirmed_complete')
    if schedule['G_ID']!=game['official_game_id'] or schedule['G_DT']!=game['game_date'] or schedule['HOME_ID']!=game['home_team_id'] or schedule['AWAY_ID']!=game['away_team_id']:raise ValueError('schedule_mismatch')
    hitters={};pitchers={};pitcher_quality={}
    for side,hi,pi in [('away',1,0),('home',2,1)]:
        hitters[side]=hitter_table(tables[hi],game,side,hitter_index)
        pitchers[side],pitcher_quality[side]=pitcher_table(tables[3+pi],table_json(sc['arrPitcher'][pi]['table']),game,side,pitcher_index,archive_index)
    scores={'home':number(schedule.get('B_SCORE_CN')),'away':number(schedule.get('T_SCORE_CN'))}
    run_checks={}
    for side,other in [('home','away'),('away','home')]:
        bat_runs=[p['R'] for p in hitters[side]];pit_runs=[p['R'] for p in pitchers[other]]
        run_checks[side]={'score':scores[side],
            'batter_runs_match_score':sum(bat_runs)==scores[side] if scores[side] is not None and all(v is not None for v in bat_runs) else None,
            'opponent_pitcher_runs_match_score':sum(pit_runs)==scores[side] if scores[side] is not None and all(v is not None for v in pit_runs) else None}
    return {'schema':'kbo-official-postgame-stat-v1','fixture_id':game['fixture_id'],'official_game_id':game['official_game_id'],
      'game_date':game['game_date'],'team_ids':{'home':game['home_team_id'],'away':game['away_team_id']},
      'series_id':game['series_id'],'header_no':game['header_no'],'completed':True,'status':'ok',
      'time_basis':'retrospective_completed_by_next_day_assumption_unverified','historical_receipt_proven':False,
      'target_game_outcome_not_feature':True,'statistics':{'batters':hitters,'pitchers':pitchers}, 'quality':{'pitchers':pitcher_quality,'run_checks':run_checks},
      'outcome':{'home_score':scores['home'],'away_score':scores['away'],
                 'namespace':'postgame_label_never_current_target_feature'}}

def run(args):
    source=Path(args.lineups).read_bytes()
    if sha(source)!=SOURCE_SHA:raise ValueError('lineup_source_sha_mismatch')
    games=[json.loads(x) for x in source.splitlines() if x.strip()]
    if len(games)!=726:raise ValueError('lineup_denominator_mismatch')
    target=[g for g in games if '20260727'<=g['game_date']<='20261027']
    if len(target)!=256 or len({g['official_game_id'] for g in target})!=256:raise ValueError('window_denominator_mismatch')
    hi,pi=indexes(games)
    archive_raw=Path(args.pitcher_archive).read_bytes() if args.pitcher_archive else None
    archive_index=archived_pitcher_index(games,args.lineup_raw,json.loads(archive_raw)) if archive_raw else {}
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True);file=out/'records.jsonl'
    prior=[json.loads(x) for x in file.read_text(encoding='utf8').splitlines()] if file.exists() else []
    done={x['official_game_id'] for x in prior}
    if len(done)!=len(prior):raise ValueError('duplicate_checkpoint')
    f=Fetcher(out,args.max_requests,args.offline);stop='complete'; completed=0
    for g in target:
        gid=g['official_game_id']
        if gid in done or (args.game and gid not in args.game):continue
        params={'leId':g['league_id'],'srId':g['series_id'],'seasonId':g['season_id'],'gameId':gid}
        bm=sm=None
        try:
            schedule_meta=g['schedule_evidence'];p=Path(args.lineup_raw)/schedule_meta['sha256'];raw=p.read_bytes()
            if sha(raw)!=schedule_meta['sha256']:raise ValueError('schedule_raw_sha')
            schedule=json.loads(raw);match=[x for x in schedule['game'] if x['G_ID']==gid]
            if len(match)!=1:raise ValueError('schedule_not_unique')
            box,bm=f.post(BOX,params);scroll,sm=f.post(SCROLL,params)
            record=normalize(g,match[0],box,scroll,hi,pi,archive_index)
            record['evidence']={'box':bm,'scroll':sm,'schedule':schedule_meta,'source_lineup_sha256':SOURCE_SHA}
        except Stop as exc:stop=str(exc);break
        except (ValueError,KeyError,TypeError,IndexError) as exc:
            record={'official_game_id':gid,'fixture_id':g['fixture_id'],'game_date':g['game_date'],'status':'unresolved','reason':str(exc)[:200],
                    'evidence':{'box':bm,'scroll':sm,'schedule':g.get('schedule_evidence'),'source_lineup_sha256':SOURCE_SHA}}
        with file.open('a',encoding='utf8',newline='\n') as stream:stream.write(json.dumps(record,ensure_ascii=False,sort_keys=True)+'\n')
        completed+=1
    captured=[json.loads(x) for x in file.read_text(encoding='utf8').splitlines()] if file.exists() else []
    counts={}
    for row in captured:counts[row['status']]=counts.get(row['status'],0)+1
    info={'input_denominator':len(games),'window_denominator':len(target),'window_start':'20260727','window_end':'20261027',
          'processed':len(captured),'remaining':len(target)-len(captured),'statuses':counts,'new_requests':f.n,'stop_reason':stop,
          'source_lineups_sha256':SOURCE_SHA,'pitcher_archive_sha256':sha(archive_raw) if archive_raw else None,
          'records_sha256':sha(file.read_bytes()) if file.exists() else None,
          'historical_receipt_proven':False,'feature_rule':'completed game_date < target_date; retrospective completed-by-next-day assumption'}
    (out/'manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(info,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--lineups',required=True);p.add_argument('--lineup-raw',required=True);p.add_argument('--output',required=True);p.add_argument('--pitcher-archive')
    p.add_argument('--max-requests',type=int,default=600);p.add_argument('--game',action='append');p.add_argument('--offline',action='store_true');a=p.parse_args()
    if not 1<=a.max_requests<=600:p.error('request limit must be 1..600')
    with capture_lock(Path(a.output)):run(a)
