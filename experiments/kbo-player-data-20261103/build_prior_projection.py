"""Generate immutable prior-only scenarios for retrospective KBO diagnosis.

Input features are histories, not fitted numerical strengths. Historical day-only
completion is an explicit assumption. No actual receipt/completion time is invented.
"""
import gzip,hashlib,json,os,re
from collections import defaultdict,Counter
from datetime import date,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
GAMES=ROOT/'data/model_candidates/kbo/archived-games.jsonl.gz'
BAT=ROOT/'data/raw/detail/kbo_batters_indiv_2023_2026.json'
PIT=ROOT/'data/raw/detail/kbo_baseball_2023_2026.json'
ST=ROOT/'data/raw/kbo_starters.json'
ROWS=OUT/'rows.jsonl'; LABELS=OUT/'labels.jsonl'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def innings_outs(value):
    """Baseball innings: 5.2 or 5 ⅔ denotes 17 outs, never 5.2 decimal innings."""
    text=str(value).strip()
    m=re.fullmatch(r'(\d+)(?:\.(\d)|\s*([⅓⅔]))?',text)
    if not m:return None
    fraction=m[2] or {'⅓':'1','⅔':'2'}.get(m[3], '0')
    if fraction not in ('0','1','2'):return None
    return 3*int(m[1])+int(fraction)


def lineup(box, side):
    slots=defaultdict(list)
    for item in ((box or {}).get('data') or {}).get(side,[]):
        if type(item.get('order')) is int and 1<=item['order']<=9 and item.get('code') and item.get('pos')!='교':
            slots[item['order']].append(str(item['code']))
    if set(slots)!=set(range(1,10)):return None
    ids=[slots[i][0] for i in range(1,10)]
    return ids if len(set(ids))==9 else None


def value(v, ids=(), latest=None, basis='prior_calendar_day_assumed_completed_not_received'):
    return {'value':v,'source_fixture_ids':list(ids),'latest_source_date':latest,'completed_at':None,
            'received_at':None,'availability_basis':basis}


def starter_from(pitch_box,starter,side):
    ps=((pitch_box or {}).get('data') or {}).get(side,[])
    if not ps or not ps[0].get('pcode'):return None
    claimed=(starter or {}).get(side+'_starter')
    if claimed and ps[0].get('name')!=claimed:return None
    return str(ps[0]['pcode'])


def scenario(target, team_games, batter_history, pitcher_history):
    day=target['date']; today=date.fromisoformat(day); features={}; projection={}
    for side in ('home','away'):
        team=target[side]
        hist=team_games.get(team,[]); recent=hist[-10:]; latest=hist[-1]['date'] if hist else None
        features[side+'_team_strength']=value([{'fixture_id':x['id'],'date':x['date'],'runs_for':x['runs_for'],'runs_against':x['runs_against']} for x in recent] if recent else None,[x['id'] for x in recent],latest)
        features[side+'_rest_days']=value((today-date.fromisoformat(latest)).days-1 if latest else None,[x['id'] for x in hist if x['date']==latest],latest)
        latest_games=[x for x in hist if x['date']==latest] if latest else []
        last=latest_games[0] if len(latest_games)==1 else None
        roster=last['lineup'] if last else None
        projection[side+'_lineup']={'player_ids':roster,'source_fixture_ids':[last['id']] if roster else [],'basis':'projected_from_prior_games','selected_by':'most recent team game on a uniquely represented previous date'}
        profiles=[]; allids=set(); alldates=[]
        if roster:
            for player in roster:
                history=batter_history.get(player,[])[-10:]
                profiles.append({'player_id':player,'history':history or None})
                for h in history:allids.add(h['fixture_id']);alldates.append(h['date'])
        features[side+'_lineup_strength']=value(profiles if roster else None,sorted(allids),max(alldates,default=None))
        # Last five distinct observed prior starters, choose the least recently used;
        # tied oldest dates are ambiguous and yield no projected pitcher.
        appearances={}
        for game in reversed(hist):
            pid=game.get('starter_id')
            if pid and pid not in appearances:appearances[pid]=game
            if len(appearances)==5:break
        starter=None
        if len(appearances)==5:
            oldest=min(x['date'] for x in appearances.values()); chosen=[pid for pid,x in appearances.items() if x['date']==oldest]
            if len(chosen)==1:starter=chosen[0]
        evidence=[{'player_id':pid,'fixture_id':game['id'],'date':game['date']} for pid,game in sorted(appearances.items())]
        projection[side+'_starter']={'player_id':starter,'rotation_candidates':evidence,'basis':'projected_from_prior_games','selected_by':'least recent last start among five most recent distinct starters; tied date missing'}
        starts=pitcher_history.get(starter,[])[-10:] if starter else []
        features[side+'_starter_strength']=value({'projected_player_id':starter,'appearances':starts} if starts else None,[x['fixture_id'] for x in starts],max((x['date'] for x in starts),default=None))
        # Only earlier calendar dates, irrespective of assumed actual hours.
        window=[x for x in hist if 1 <= (today-date.fromisoformat(x['date'])).days <= 2]
        complete=bool(window) and all(x['relief'] is not None for x in window)
        features[side+'_bullpen_load']=value({'kind':'previous_1_2_calendar_days_relief_appearances_and_outs_proxy','schedule_coverage_verified':False,'games':[{k:x[k] for k in ('id','date','relief')} for x in window]} if complete else None,[x['id'] for x in window],max((x['date'] for x in window),default=None))
    homehist=team_games.get(target['home'],[]); parks=[x for x in homehist if x['home_venue']]
    lastpark=parks[-1] if parks else None
    features['park_factor']=value({'known_prior_home_venue':lastpark['home_venue'],'numeric_park_factor':None} if lastpark else None,[lastpark['id']] if lastpark else [],lastpark['date'] if lastpark else None)
    return {'fixture_id':target['source_fixture_id'],'availability_tier':'retrospective_unproven',
            'scenario':'projected_from_prior_games','projection':projection,'features':features,
            'completed_at':None,'received_at':None,'warning':'No official pregame lineup/starter or contemporaneous data receipt; scenario is not point-in-time verified.'}


def generate(games,batters,pitchers,starters):
    byday=defaultdict(list)
    for g in games:byday[g['date']].append(g)
    teams=defaultdict(list); bat=defaultdict(list); pit=defaultdict(list); result=[]
    for day,fixtures in sorted(byday.items()):
        # Target-day boxscores, players and labels are not read here.
        for g in sorted(fixtures,key=lambda x:x['source_fixture_id']):result.append(scenario(g,teams,bat,pit))
        for g in sorted(fixtures,key=lambda x:x['source_fixture_id']):
            ident=g['source_fixture_id']; b=batters.get(ident); p=pitchers.get(ident); st=starters.get(ident)
            for side in ('home','away'):
                club=g[side]; ps=((p or {}).get('data') or {}).get(side,[]); player_rows=((b or {}).get('data') or {}).get(side,[])
                rs=[]
                for x in ps[1:]:
                    outs=innings_outs(x.get('inn'))
                    if not x.get('pcode') or outs is None:rs=None;break
                    rs.append({'player_id':str(x['pcode']),'outs':outs,'source_bf_raw':x.get('bf')})
                # ps absent is unknown, not zero relief appearances.
                summary={'appearances':len(rs),'outs':sum(x['outs'] for x in rs),'pitchers':rs,'source_bf_units':'unverified'} if ps and rs is not None else None
                teams[club].append({'id':ident,'date':day,'runs_for':g['hg' if side=='home' else 'ag'],'runs_against':g['ag' if side=='home' else 'hg'],
                    'lineup':lineup(b,side),'starter_id':starter_from(p,st,side),'relief':summary,
                    'home_venue':((b or {}).get('data') or {}).get('stadium') if side=='home' else None})
                for x in player_rows:
                    if x.get('code'):
                        bat[str(x['code'])].append({'fixture_id':ident,'date':day,**{k:x.get(k) for k in ('ab','hit','bb','hr')}})
                for x in ps:
                    if x.get('pcode'):
                        pit[str(x['pcode'])].append({'fixture_id':ident,'date':day,'outs':innings_outs(x.get('inn')),
                            **{k:x.get(k) for k in ('r','er','hit','bb','hr','kk')},'source_bf_raw':x.get('bf'),'source_bf_units':'unverified'})
    return result


def main():
    inv=json.loads((OUT/'inventory.json').read_text(encoding='utf8'))
    for name,path in [('rows.jsonl',ROWS),('labels.jsonl',LABELS)]:
        if sha(path)!=inv['output_sha256'][name]:raise ValueError('original input changed: '+name)
    for key,path in [('games',GAMES),('batters',BAT),('pitchers',PIT),('starters',ST)]:
        if sha(path)!=inv['source_hashes'][key]:raise ValueError('source input changed: '+key)
    with gzip.open(GAMES,'rt',encoding='utf8') as f: games=[json.loads(x) for x in f]
    batters=json.loads(BAT.read_text(encoding='utf8'));pitchers=json.loads(PIT.read_text(encoding='utf8'))
    starter_list=json.loads(ST.read_text(encoding='utf8')); st=defaultdict(list)
    for x in starter_list:st[x['gameId']].append(x)
    starters={k:v[0] for k,v in st.items() if len(v)==1}
    predictions=generate(games,batters,pitchers,starters)
    if len(predictions)!=2913 or len({x['fixture_id'] for x in predictions})!=len(predictions):raise ValueError('unexpected fixture coverage')
    target=OUT/'prior-projected-features.jsonl.gz';tmp=OUT/'prior-projected-features.jsonl.gz.tmp'
    with tmp.open('wb') as stream:
        with gzip.GzipFile(filename='',mode='wb',fileobj=stream,mtime=0) as f:
            for x in predictions:f.write((json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode('utf8'))
    os.replace(tmp,target)
    counts=Counter()
    for row in predictions:
        for side in ('home','away'):
            counts[side+'_lineup_projected']+=int(row['projection'][side+'_lineup']['player_ids'] is not None)
            counts[side+'_starter_projected']+=int(row['projection'][side+'_starter']['player_id'] is not None)
            for name in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load'):
                counts[side+'_'+name+'_non_null']+=int(row['features'][side+'_'+name]['value'] is not None)
        counts['park_history']+=int(row['features']['park_factor']['value'] is not None)
    manifest={'schema_version':'kbo-player-prior-projected-v1','status':'ready_for_adapter_no_model_fit',
        'amendment':'integration-amendment.md; no claim of authenticated availability','fixture_count':len(predictions),'counts':dict(counts),'date_range':[min(g['date'] for g in games),max(g['date'] for g in games)],
        'inputs_sha256':{str(p):sha(p) for p in (ROWS,LABELS,GAMES,BAT,PIT,ST)},'output_sha256':sha(target),
        'feature_path':str(target),'hindsight_scenario':'original rows.jsonl home/away_actual_* only, never main scenario',
        'rules':['No target player identity, stats or score in projected features.','No same-date data used; prior calendar-day completion is assumed, not verified.','Most recent uniquely dated team lineup; five most recent distinct starters and least recent last start; tie is missing.','Player/team histories are last ten observations; relief proxy includes previous one or two calendar dates and is not a 24/48h pitch count.','Source bf units unverified; raw bf only. Bullpen schedule/source completeness is not independently verified.','Park identity from prior home game; no numerical park factor is invented.','Original files and historical data are not independently authenticated; pregame/price receipt unknown.']}
    tmp=OUT/'prior-projected-manifest.json.tmp';tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,OUT/'prior-projected-manifest.json')
    print(json.dumps({'rows':len(predictions),'counts':dict(counts),'output_sha256':manifest['output_sha256']},ensure_ascii=False))

if __name__=='__main__':main()
