"""H2 retrospective official-schedule pitcher overlays. Never fits or scores."""
import copy,gzip,hashlib,importlib.util,json,os,subprocess
from collections import Counter,defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
spec=importlib.util.spec_from_file_location('kbo_h1_overlay',HERE/'build_official_lineup_overlay.py');h1=importlib.util.module_from_spec(spec);spec.loader.exec_module(h1)
spec2=importlib.util.spec_from_file_location('kbo_prior_builder',HERE/'build_prior_projection.py');prior=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(prior)
CAPTURE=h1.CAPTURE;PITCH=ROOT/'data/raw/detail/kbo_baseball_2023_2026.json';GAMES=ROOT/'data/model_candidates/kbo/archived-games.jsonl.gz'
H1=HERE/'official-lineup-overlay-2026.jsonl.gz';PROJECTION=HERE/'prior-projected-features.jsonl.gz'
H1_SHA='ddc66e073ada98ef9cbf469fac64aed53e45ac3d4e44a1e4330f2c661eced627'
PITCH_SHA='c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'
GAMES_SHA='c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce'
PROTOCOL='c897a13e06209da14561ce1eb2c3c856eff6e757'


def raw_sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


def verify_inputs():
    checks=h1.verify_base()
    for path,expected,commit in [(H1,H1_SHA,'a28cf0d19003bc436d174bb85af46be86ee1ee24'),(PITCH,PITCH_SHA,h1.BASE_COMMIT),(GAMES,GAMES_SHA,h1.BASE_COMMIT)]:
        relative=path.relative_to(ROOT).as_posix();blob=subprocess.check_output(['git','cat-file','blob',commit+':'+relative],cwd=ROOT);work=path.read_bytes()
        if hashlib.sha256(work).hexdigest()!=expected or hashlib.sha256(blob).hexdigest()!=expected or blob!=work:raise ValueError('raw/Git bytes mismatch: '+relative)
        checks[relative]={'raw_sha256':expected,'git_blob_sha256':expected,'exact_bytes_equal':True}
    for name,want in [('official-lineups.jsonl',h1.EXPECTED['official']),('manifest.json',h1.EXPECTED['official_manifest']),('audit.json',h1.EXPECTED['official_audit'])]:
        if raw_sha(CAPTURE/name)!=want:raise ValueError('frozen official source changed: '+name)
    return checks


def schedule_pitchers(official,schedule):
    gid=official['official_game_id']; games=[g for g in schedule.get('game',[]) if g.get('G_ID')==gid]
    if len(games)!=1:return None,'schedule_missing_or_duplicate'
    game=games[0];result={};seen=set()
    for side,side_code,field in [('home','HOME_ID','B'),('away','AWAY_ID','T')]:
        if str(game.get(side_code))!=str(official.get(side+'_team_id')):return None,'schedule_team_mismatch'
        item=(official.get('starting_pitchers') or {}).get(side) or {};raw=game.get(field+'_PIT_P_ID');existing=item.get('player_id')
        if isinstance(raw,bool) or not str(raw or '').isdigit() or int(raw)<=0 or not isinstance(existing,str) or not existing.isdigit() or int(existing)<=0:return None,side+'_pitcher_missing_or_invalid'
        if str(raw)!=existing:return None,side+'_schedule_id_conflict'
        if existing in seen:return None,'duplicate_pitcher_across_teams'
        actual_name=str(game.get(field+'_PIT_P_NM') or '').strip();saved_name=str(item.get('name') or '').strip()
        if not actual_name or actual_name!=saved_name:return None,side+'_schedule_name_conflict'
        seen.add(existing);result[side]={'player_id':existing,'name':actual_name,'team_id':str(game[side_code]),
            'source_field':field+'_PIT_P_ID','selection_basis':'official_schedule_listed_pitcher_not_independently_confirmed_pregame'}
    return result,None


def pitcher_history(games,pitchers):
    result=defaultdict(list)
    for game in sorted(games,key=lambda x:(x['date'],x['source_fixture_id'])):
        fid=game['source_fixture_id'];box=pitchers.get(fid)
        if not box:continue
        if (box.get('date'),box.get('home'),box.get('away'))!=(game['date'],game['home'],game['away']):raise ValueError('pitcher archive identity mismatch')
        for side in ('home','away'):
            for x in (box.get('data') or {}).get(side,[]):
                if x.get('pcode'):
                    result[str(x['pcode'])].append({'fixture_id':fid,'date':game['date'],'outs':prior.innings_outs(x.get('inn')),
                        **{k:x.get(k) for k in ('r','er','hit','bb','hr','kk')},'source_bf_raw':x.get('bf'),'source_bf_units':'unverified'})
    return result


def pitcher_features(base,chosen,histories,target_date):
    output={}
    for side in ('home','away'):
        pid=chosen[side]['player_id'];past=[x for x in histories.get(pid,[]) if x['date']<target_date][-10:]
        if any(x['date']>=target_date or x['fixture_id']==base['fixture_id'] for x in past):raise ValueError('target/future pitcher leakage')
        output[side+'_starter_strength']={'value':{'projected_player_id':pid,'appearances':past} if past else None,
            'source_fixture_ids':[x['fixture_id'] for x in past],'latest_source_date':max((x['date'] for x in past),default=None),
            'completed_at':None,'received_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received'}
    return output


def apply(base,features,chosen,official,mode):
    if mode not in ('official_starter_only','official_batting_and_starter'):raise ValueError('invalid variant')
    if set(features)!={'home_starter_strength','away_starter_strength'}:raise ValueError('invalid feature replacement')
    out=copy.deepcopy(base)
    for key in features:out['features'][key]=copy.deepcopy(features[key])
    out['scenario']=mode
    out['official_schedule_pitcher_context']={'pitchers':copy.deepcopy(chosen),'official_game_id':official['official_game_id'],
        'game_date':official['game_date'],'schedule_evidence':official['schedule_evidence'],'historical_receipt_proven':False,
        'eligible_as_pregame_feature':False,'point_in_time_verified':False,
        'basis':'retrospective_official_schedule_listed_actual_starter_assumed_available',
        'baseline_projection_metadata_not_official_selector':True}
    out['warning']='Retrospective assumption: schedule-listed pitchers were actual starters available before game; historical publication/receipt unproven.'
    return out


def nonstarter_hash(rows):
    h=hashlib.sha256()
    for row in rows:
        v={'fixture_id':row['fixture_id'],'features':{k:x for k,x in row['features'].items() if k not in ('home_starter_strength','away_starter_strength')}}
        h.update(canon(v)+b'\n')
    return h.hexdigest()


def save_gzip(path,rows):
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as f:
            for row in rows:f.write(canon(row)+b'\n')
    os.replace(temp,path)


def main():
    checks=verify_inputs()
    with (HERE/'rows.jsonl').open(encoding='utf8') as f:rows=h1.unique_index(map(json.loads,f))
    with gzip.open(PROJECTION,'rt',encoding='utf8') as f:projection=h1.unique_index(map(json.loads,f))
    with gzip.open(H1,'rt',encoding='utf8') as f:batting=h1.unique_index(map(json.loads,f))
    with gzip.open(GAMES,'rt',encoding='utf8') as f:games=list(map(json.loads,f))
    dates={g['source_fixture_id']:g['date'] for g in games}
    pitchers=json.loads(PITCH.read_text(encoding='utf8')); histories=pitcher_history(games,pitchers)
    source=[json.loads(x) for x in (CAPTURE/'official-lineups.jsonl').read_text(encoding='utf8').splitlines()]
    if len(source)!=533 or len({x['fixture_id'] for x in source})!=533:raise ValueError('duplicate official fixture')
    source2026=[x for x in source if x.get('season_id')==2026]
    if len(source2026)!=193 or set(batting)!={x['fixture_id'] for x in source2026}:raise ValueError('H1 cohort mismatch')
    h2=[];both=[];excluded=Counter();chosen_by_id={};source_by_id={};nonlineup_before=[];nonlineup_batting=[]
    for official in source2026:
        fid=official['fixture_id'];source=rows[fid]
        h1.validate(official,source) # Verify raw schedule, lineups, profile/team/date/header and H1 identity.
        schedule=json.loads(h1.check_evidence(official['schedule_evidence']))
        chosen,reason=schedule_pitchers(official,schedule)
        if reason:excluded[reason]+=1;continue
        base=projection[fid];h1row=batting[fid];target_date=dates[fid]
        feat=pitcher_features(base,chosen,histories,target_date)
        for f in feat.values():
            for id_ in f['source_fixture_ids']:
                if id_ not in dates or dates[id_]>=target_date:raise ValueError('history source outside prior calendar')
        h2.append(apply(base,feat,chosen,official,'official_starter_only'))
        both.append(apply(h1row,feat,chosen,official,'official_batting_and_starter'))
        nonlineup_before.append(base);nonlineup_batting.append(h1row)
        chosen_by_id[fid]=chosen;source_by_id[fid]=official
    def ordered(items):return sorted(items,key=lambda x:(dates[x['fixture_id']],x['fixture_id']))
    h2=ordered(h2);both=ordered(both);nonlineup_before=ordered(nonlineup_before);nonlineup_batting=ordered(nonlineup_batting)
    if nonstarter_hash(h2)!=nonstarter_hash(nonlineup_before) or nonstarter_hash(both)!=nonstarter_hash(nonlineup_batting):raise ValueError('non-starter features changed')
    # Cross-check uses target boxscores for diagnostic counts only, after selection and feature assembly.
    audit=Counter();missing_history=Counter()
    for row in h2:
        fid=row['fixture_id'];box=pitchers.get(fid)
        absent=[row['features'][s+'_starter_strength']['value'] is None for s in ('home','away')]
        missing_history['either_team_missing_games']+=int(any(absent));missing_history['both_teams_missing_games']+=int(all(absent))
        for side in ('home','away'):
            ps=((box or {}).get('data') or {}).get(side,[]);first=str(ps[0].get('pcode') or '') if ps else ''
            official_id=chosen_by_id[fid][side]['player_id'];tag='postgame_missing' if not first else 'match' if first==official_id else 'mismatch';audit[tag]+=1
            missing_history[side+'_missing']+=int(row['features'][side+'_starter_strength']['value'] is None)
    for key in ('match','mismatch','postgame_missing'):audit.setdefault(key,0)
    paths={'official_starter_only':HERE/'official-starter-only-2026.jsonl.gz','official_batting_and_starter':HERE/'official-batting-and-starter-2026.jsonl.gz'}
    save_gzip(paths['official_starter_only'],h2);save_gzip(paths['official_batting_and_starter'],both)
    manifest={'schema_version':'kbo-official-pitcher-factorial-v1','status':'retrospective_assumption_only_no_training_no_scores',
       'protocol_commit':PROTOCOL,'factorial_contract_commits':['38d25ef3','e03addcb'],'official_source_sha256':h1.EXPECTED['official'],'h1_sha256':H1_SHA,
       'exact_input_git_blob_checks':checks,'official_2026_eligible_for_check':len(source2026),'common_cohort':len(h2),'excluded_games':sum(excluded.values()),'exclusion_reasons':dict(excluded),
       'prior_starter_history_missing':dict(missing_history),'postgame_first_pitcher_crosscheck_sides':dict(audit),
       'postgame_crosscheck_affects_selection':False,'point_in_time_verified':0,'input_unchanged_nonstarter_hash':nonstarter_hash(nonlineup_before),
       'starter_only_nonstarter_hash':nonstarter_hash(h2),'h1_unchanged_nonstarter_hash':nonstarter_hash(nonlineup_batting),'both_nonstarter_hash':nonstarter_hash(both),
       'outputs_sha256':{name:raw_sha(path) for name,path in paths.items()},
       'limitations':['Only 3 official examples independently checked by name, ID matching archived postgame record is not independent official certification.','Official schedule fields are retrospective assumptions of actual starter; publication/receipt timing and changes unproven.','Postgame first pitcher crosscheck is diagnostic only and does not select/filter the cohort.','Historical prior dates assumed completed; source BF units unknown; no target-day and future stats.','No model fitting, predictions, returns, production or wallet changes.','H1 primary 193 game cohort remains unchanged; 2x2 is a secondary comparison on the common set.']}
    p=HERE/'official-pitcher-factorial-manifest.json';tmp=HERE/'official-pitcher-factorial-manifest.json.tmp';tmp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,p)
    print(json.dumps({k:manifest[k] for k in ('official_2026_eligible_for_check','common_cohort','excluded_games','prior_starter_history_missing','postgame_first_pitcher_crosscheck_sides','outputs_sha256')},ensure_ascii=False))

if __name__=='__main__':main()
