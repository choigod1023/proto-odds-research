"""Extend frozen four-arm official-information scenarios to full 2026.

No model fitting or scoring; current official material is retrospective only.
"""
import copy,gzip,hashlib,importlib.util,json,math,os,statistics,subprocess
from functools import lru_cache
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
KLROOT=Path(r'C:\Users\user\Documents\Codex\klu')
CAPTURE=KLROOT/'.research-state/kbo-lineups-v5'
SOURCE=KLROOT/'experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl'
SOURCE_SHA='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3'
SOURCE_COMMIT='b24739cf52435070586f8687011fd473a16123e4'
PROTOCOL='97b76a9c7a605a4ba3526530b1d4fdabb320f640'

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
h1=load_module('prior_official_lineup',HERE/'build_official_lineup_overlay.py')
h2=load_module('prior_official_pitcher',HERE/'build_official_pitcher_factorial.py')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')


def verify_frozen_inputs():
    checks=h2.verify_inputs()
    # h2.verify_inputs uses its own h1 instance and therefore verifies preserved v4.
    work=SOURCE.read_bytes();rel=SOURCE.relative_to(KLROOT).as_posix()
    blob=subprocess.check_output(['git','cat-file','blob',SOURCE_COMMIT+':'+rel],cwd=KLROOT)
    if digest(SOURCE)!=SOURCE_SHA or work!=blob:raise ValueError('full official checkout/blob SHA mismatch')
    for name,expected in [('official-starter-only-2026.jsonl.gz','beda5b82e7cd9045e01fdb09b5b27061ce2f82cd58b568b057bd119f37609fce'),('official-batting-and-starter-2026.jsonl.gz','4306cc9b429446de30677a4916577ac0680480d36aa2cdd509eb487e20a0cb7a')]:
        path=HERE/name;relative=path.relative_to(ROOT).as_posix();raw=path.read_bytes();tracked=subprocess.check_output(['git','cat-file','blob','7a11192:'+relative],cwd=ROOT)
        if hashlib.sha256(raw).hexdigest()!=expected or raw!=tracked:raise ValueError('fixed H2 raw/Git mismatch: '+name)
        checks[relative]={'raw_sha256':expected,'git_blob_sha256':hashlib.sha256(tracked).hexdigest(),'exact_bytes_equal':True}
    for name in ('inventory.json','prior-projected-manifest.json','official-lineup-overlay-2026-manifest.json','official-pitcher-factorial-manifest.json'):
        path=HERE/name;relative=path.relative_to(ROOT).as_posix();raw=path.read_bytes();tracked=subprocess.check_output(['git','cat-file','blob','7a11192:'+relative],cwd=ROOT)
        if raw.replace(b'\r\n',b'\n')!=tracked:raise ValueError('fixed manifest content mismatch: '+name)
        checks[relative]={'raw_sha256':hashlib.sha256(raw).hexdigest(),'git_blob_sha256':hashlib.sha256(tracked).hexdigest(),'byte_policy':'explicit CRLF to LF for text manifest only'}
    return checks|{'full_official_source':{'raw_sha256':SOURCE_SHA,'git_blob_sha256':hashlib.sha256(blob).hexdigest(),'exact_bytes_equal':True}}


def inspect_official(record,source):
    """Validate source before selecting a lineup or pitcher, never using target boxscore."""
    fid=source['fixture_id'];gid=record.get('official_game_id');day=record.get('game_date')
    if record.get('fixture_id')!=fid or record.get('season_id')!=2026 or record.get('league_id')!=1:raise ValueError('fixture_or_season_mismatch')
    if not isinstance(day,str) or len(day)!=8 or not day.isdigit() or not str(gid).startswith(day):raise ValueError('date_mismatch')
    source_day=f'{day[:4]}-{day[4:6]}-{day[6:]}'
    if source.get('season')!=2026:raise ValueError('source_season_mismatch')
    basis=record.get('fixture_match_basis')
    if basis=='exact_official_game_id':
        if fid!=str(gid)+'2026':raise ValueError('official_game_id_mismatch')
    elif basis=='unique_date_home_away_schedule_match':
        if fid!=f"score:{source_day}:{source['home_id']}:{source['away_id']}":raise ValueError('score_fixture_identity_mismatch')
    else:raise ValueError('unsupported_crosswalk')
    if source.get('kickoff_at') and source['kickoff_at'][:10]!=source_day:raise ValueError('kickoff_date_mismatch')
    if record.get('eligible_as_pregame_feature') is not False or record.get('historical_receipt_proven') is not False or record.get('historical_publication_time_proven') is not False:raise ValueError('historical_receipt_incorrectly_claimed')
    schedule=json.loads(h1.check_evidence(record['schedule_evidence']));games=schedule.get('game',[])
    exact=[g for g in games if g.get('G_ID')==gid]
    if len(exact)!=1:raise ValueError('ambiguous_official_game_id')
    game=exact[0]
    for key,value in [('G_ID',gid),('G_DT',day),('LE_ID',1),('SEASON_ID',2026),('SR_ID',record['series_id']),('HEADER_NO',record['header_no']),('HOME_ID',record['home_team_id']),('AWAY_ID',record['away_team_id'])]:
        if str(game.get(key))!=str(value):raise ValueError('schedule_mismatch_'+key)
    if game.get('HOME_ID')==game.get('AWAY_ID'):raise ValueError('duplicate_team')
    home=str(game.get('HOME_NM','')).strip();away=str(game.get('AWAY_NM','')).strip()
    if (home,away)!=(source['home_id'],source['away_id']):raise ValueError('source_team_mismatch')
    if source.get('kickoff_at') and source['kickoff_at'][11:16]!=str(game.get('G_TM')):raise ValueError('source_clock_mismatch')
    if basis=='unique_date_home_away_schedule_match':
        same=[g for g in games if str(g.get('G_DT'))==day and str(g.get('HOME_NM','')).strip()==home and str(g.get('AWAY_NM','')).strip()==away]
        if len(same)!=1:raise ValueError('doubleheader_without_identity')
    if record['lineup_evidence'].get('params',{}).get('gameId')!=gid:raise ValueError('wrong_lineup_request')
    body=json.loads(h1.check_evidence(record['lineup_evidence']))
    if not isinstance(body,list) or len(body)!=5 or not body[0] or body[0][0].get('LINEUP_CK') is not True or record.get('lineup_flag') is not True:raise ValueError('official_lineup_not_true')
    if record.get('lineup_status')=='official_flag_true_player_ids_unavailable':return None,None,'lineup_player_ids_unavailable'
    if record.get('lineup_status')!='official_flag_true_profile_name_crossmatched':raise ValueError('unrecognised_lineup_status')
    lineups={};seen=set()
    accepted={'postgame_same_game_side_order_and_official_profile_name','postgame_other_game_same_team_season_name_and_official_profile_name'}
    for side,meta_index,table_index in [('home',1,3),('away',2,4)]:
        meta=body[meta_index][0]
        for key,v in [('G_ID',gid),('T_ID',game[side.upper()+'_ID']),('LE_ID',1),('SEASON_ID',2026),('SR_ID',record['series_id'])]:
            if str(meta.get(key))!=str(v):raise ValueError('lineup_mismatch_'+key)
        official_players=record.get('lineups',{}).get(side,[])
        raw_players=json.loads(body[table_index][0])['rows']
        original=sorted((int(x['row'][0]['Text']),x['row'][2]['Text'].strip()) for x in raw_players)
        if len(official_players)!=9 or len(original)!=9 or [x[0] for x in original]!=list(range(1,10)) or sorted(x.get('order') for x in official_players)!=list(range(1,10)):raise ValueError('invalid_lineup_order')
        selected=[]
        for player,(order,name) in zip(sorted(official_players,key=lambda x:x['order']),original):
            pid=player.get('player_id');e=player.get('official_profile_evidence') or {}
            if player['order']!=order or player.get('name')!=name or not isinstance(pid,str) or not pid.isdigit() or int(pid)<=0 or pid in seen:raise ValueError('invalid_or_duplicate_player')
            if player.get('player_id_basis') not in accepted:raise ValueError('unverified_player_mapping_basis')
            if str((e.get('params') or {}).get('playerId'))!=pid or h1.profile_name(h1.check_evidence(e))!=name:raise ValueError('official_profile_mismatch')
            seen.add(pid);selected.append({'order':order,'player_id':pid,'name':name,'player_id_basis':player['player_id_basis'],'profile_evidence':e})
        lineups[side]=selected
    pitchers,reason=h2.schedule_pitchers(record,schedule)
    return lineups,pitchers,reason


def gap_summary(gaps,missing):
    s=sorted(gaps);n=len(s)
    return {'observed':n,'no_history':missing,'median_days':statistics.median(s) if n else None,'p90_nearest_rank_days':s[math.ceil(n*.90)-1] if n else None,'max_days':max(s) if n else None,
            'greater_than_14_days':sum(x>14 for x in s),'greater_than_30_days':sum(x>30 for x in s),'greater_than_60_days':sum(x>60 for x in s),'greater_than_90_days':sum(x>90 for x in s)}


def hash_core(rows):
    h=hashlib.sha256()
    for row in rows:
        data={'fixture_id':row['fixture_id'],'features':{k:v for k,v in row['features'].items() if k not in ('home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength')}}
        h.update(canon(data)+b'\n')
    return h.hexdigest()


def main():
    h1.CAPTURE=CAPTURE;h1.source_bytes.cache_clear();h1.profile_name=lru_cache(maxsize=8192)(h1.profile_name)
    checks=verify_frozen_inputs();manifest=json.loads((CAPTURE/'manifest.json').read_text(encoding='utf8'));audit=json.loads((CAPTURE/'audit.json').read_text(encoding='utf8'))
    v5path=CAPTURE/'official-lineups.jsonl'
    if digest(v5path)!=manifest['lineups_sha256'] or audit['lineups_sha256']!=manifest['lineups_sha256']:raise ValueError('v5 manifest/audit mismatch')
    source=[json.loads(x) for x in SOURCE.read_bytes().splitlines()]
    if len(source)!=726 or len({x['fixture_id'] for x in source})!=726 or len({x['official_game_id'] for x in source})!=726:raise ValueError('duplicate or incomplete official source')
    with (HERE/'rows.jsonl').open(encoding='utf8') as f:rows=h1.unique_index(map(json.loads,f))
    with gzip.open(h2.PROJECTION,'rt',encoding='utf8') as f:base=h1.unique_index(map(json.loads,f))
    with gzip.open(h2.H1,'rt',encoding='utf8') as f:old_h1=h1.unique_index(map(json.loads,f))
    with gzip.open(HERE/'official-starter-only-2026.jsonl.gz','rt',encoding='utf8') as f:old_h2=h1.unique_index(map(json.loads,f))
    with gzip.open(HERE/'official-batting-and-starter-2026.jsonl.gz','rt',encoding='utf8') as f:old_both=h1.unique_index(map(json.loads,f))
    with gzip.open(h2.GAMES,'rt',encoding='utf8') as f:games=list(map(json.loads,f))
    dates={g['source_fixture_id']:g['date'] for g in games}
    batters=json.loads(h1.BAT.read_text(encoding='utf8'));pitchers=json.loads(h2.PITCH.read_text(encoding='utf8'))
    batter_index=h1.index_history(games,batters);pitcher_index=h2.pitcher_history(games,pitchers)
    results={'official_batting_only':[],'official_starter_only':[],'official_batting_and_starter':[]};original=[];excluded=Counter();excluded_records=[];selected_ids=[];selected_sources={}
    for item in source:
        fid=item['fixture_id']
        if fid not in rows or fid not in base or fid not in dates:raise ValueError('official fixture absent in frozen base')
        selected,pitch,reason=inspect_official(item,rows[fid])
        if reason:
            excluded[reason]+=1;excluded_records.append({'fixture_id':fid,'reason':reason});continue
        day=dates[fid];od=item['game_date']
        if day!=f'{od[:4]}-{od[4:6]}-{od[6:]}':raise ValueError('source_archive_date_mismatch')
        hrow=h1.replace_lineups(base[fid],selected,batter_index,day,item)
        hrow['official_lineup_context'].update(fixture_match_basis=item['fixture_match_basis'],header_no=item['header_no'],series_id=item['series_id'])
        f=h2.pitcher_features(base[fid],pitch,pitcher_index,day)
        starter=h2.apply(base[fid],f,pitch,item,'official_starter_only')
        both=h2.apply(hrow,f,pitch,item,'official_batting_and_starter')
        for variant in (starter,both):variant['official_schedule_pitcher_context'].update(fixture_match_basis=item['fixture_match_basis'],header_no=item['header_no'],series_id=item['series_id'])
        for side in ('home','away'):
            for player in hrow['features'][side+'_lineup_strength']['value']:
                for x in player['history'] or []:
                    if dates.get(x['fixture_id'])!=x['date'] or x['date']>=day:raise ValueError('invalid_batter_history_date')
            for past in (f[side+'_starter_strength']['value'] or {}).get('appearances',[]):
                if dates.get(past['fixture_id'])!=past['date'] or past['date']>=day:raise ValueError('invalid_pitcher_history_date')
        original.append(base[fid]);results['official_batting_only'].append(hrow);results['official_starter_only'].append(starter);results['official_batting_and_starter'].append(both)
        selected_ids.append(fid);selected_sources[fid]=pitch
    if sum(excluded.values())+len(original)!=726:raise ValueError('incorrect selection accounting')
    order=lambda items:sorted(items,key=lambda x:(dates[x['fixture_id']],x['fixture_id']))
    original=order(original);results={k:order(v) for k,v in results.items()}
    ordered_ids=[x['fixture_id'] for x in original]
    if len(set(ordered_ids))!=len(ordered_ids) or any([x['fixture_id'] for x in v]!=ordered_ids for v in results.values()):raise ValueError('cohort mismatch')
    core=hash_core(original)
    if any(hash_core(v)!=core for v in results.values()):raise ValueError('nonplayer feature changed')
    for a,b,c,d in zip(original,results['official_batting_only'],results['official_starter_only'],results['official_batting_and_starter']):
        if any(a['features'][s+'_starter_strength']!=b['features'][s+'_starter_strength'] or a['features'][s+'_lineup_strength']!=c['features'][s+'_lineup_strength'] or b['features'][s+'_lineup_strength']!=d['features'][s+'_lineup_strength'] or c['features'][s+'_starter_strength']!=d['features'][s+'_starter_strength'] for s in ('home','away')):raise ValueError('factorial features changed')
    old=set(old_h1);common=set(ordered_ids); overlap=old&common;complement=common-old
    for f in overlap:
        ix=ordered_ids.index(f)
        if results['official_batting_only'][ix]['features']!=old_h1[f]['features'] or results['official_starter_only'][ix]['features']!=old_h2[f]['features'] or results['official_batting_and_starter'][ix]['features']!=old_both[f]['features']:raise ValueError('previous 193 features differ')
    gapsets={k:defaultdict(list) for k in ('batters','pitchers')};missing={k:Counter() for k in gapsets}
    for br,pr in zip(results['official_batting_only'],results['official_starter_only']):
        day=date.fromisoformat(dates[br['fixture_id']]);period=day.isoformat()[:7]; group='original_193' if br['fixture_id'] in old else 'new_complement'
        for side in ('home','away'):
            for player in br['features'][side+'_lineup_strength']['value']:
                hist=player['history']
                for group in ('overall',period,'original_193' if br['fixture_id'] in old else 'new_complement'):
                    if hist:gapsets['batters'][group].append((day-date.fromisoformat(max(x['date'] for x in hist))).days)
                    else:missing['batters'][group]+=1
            hist=(pr['features'][side+'_starter_strength']['value'] or {}).get('appearances')
            for group in ('overall',period,'original_193' if br['fixture_id'] in old else 'new_complement'):
                if hist:gapsets['pitchers'][group].append((day-date.fromisoformat(max(x['date'] for x in hist))).days)
                else:missing['pitchers'][group]+=1
    freshness={kind:{period:gap_summary(gapsets[kind][period],missing[kind][period]) for period in sorted(set(gapsets[kind])|set(missing[kind]))} for kind in gapsets}
    source_last={}
    for label,raw in [('batters',batters),('pitchers',pitchers)]:
        dates_all=[v.get('date') for v in raw.values() if isinstance(v,dict) and v.get('date')]
        dates_used=[dates[k] for k in raw if k in dates]
        source_last[label]={'all_source_latest_date':max(dates_all,default=None),'archive_linked_latest_date':max(dates_used,default=None),'source_rows':len(raw)}
    target_after_last={kind:sum(dates[fid]>meta['archive_linked_latest_date'] for fid in ordered_ids) for kind,meta in source_last.items()}
    postcheck=Counter()
    for fid in ordered_ids:
        p=pitchers.get(fid)
        for side in ('home','away'):
            ps=((p or {}).get('data') or {}).get(side,[]);first=str(ps[0].get('pcode') or '') if ps else ''
            postcheck['missing' if not first else 'match' if first==selected_sources[fid][side]['player_id'] else 'mismatch']+=1
    for k in ('match','mismatch','missing'):postcheck.setdefault(k,0)
    out_sha={}
    for key,v in results.items():
        path=HERE/('official-full-2026-'+key+'.jsonl.gz');h2.save_gzip(path,v);out_sha[key]=digest(path)
    groups={'all':ordered_ids,'original_193':sorted(overlap,key=lambda fid:(dates[fid],fid)),'new_complement':sorted(complement,key=lambda fid:(dates[fid],fid)),
            'excluded':excluded_records}
    idsfile=HERE/'official-full-2026-cohorts.jsonl';tmp=HERE/'official-full-2026-cohorts.jsonl.tmp';tmp.write_text(json.dumps(groups,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8',newline='\n');os.replace(tmp,idsfile)
    data={'schema_version':'kbo-official-information-full-2026-v1','status':'ready_retrospective_only_no_model_fit_or_scoring','protocol_commit':PROTOCOL,'parent_contract_commit':'69694632','source_commit':SOURCE_COMMIT,'source_sha256':SOURCE_SHA,'source_byte_policy':'original dataset, gzip and official rows require exact raw bytes versus pinned Git blob; four fixed text manifests compared after explicit CRLF-to-LF normalization with both SHA256 recorded',
          'v5_manifest_sha256':digest(CAPTURE/'manifest.json'),'v5_audit_sha256':digest(CAPTURE/'audit.json'),'raw_git_blob_checks':checks,'source_opportunities':726,'source_lineups_with_ids':709,'source_ids_unavailable':17,'source_player_id_basis_counts':dict(Counter(p['player_id_basis'] for x in source if x['lineup_status']=='official_flag_true_profile_name_crossmatched' for side in ('home','away') for p in x['lineups'][side])),'common_cohort':len(original),'exclusion_reasons':dict(excluded),'original_193_overlap':len(overlap),'new_complement':len(complement),'original_193_features_identical':True,
          'unchanged_nonplayer_features_sha256':core,'variant_nonplayer_hashes':{k:hash_core(v) for k,v in results.items()},'cohort_file_sha256':digest(idsfile),'output_sha256':out_sha,'source_last_dates':source_last,'selected_targets_after_source_last':target_after_last,'freshness':freshness,
          'postgame_first_pitcher_crosscheck_sides':dict(postcheck),'postgame_crosscheck_affects_selection':False,'archived_market_present':sum(rows[f]['market'] is not None for f in ordered_ids),'archived_market_missing':sum(rows[f]['market'] is None for f in ordered_ids),'price_receipt_verified':0,'point_in_time_verified':0,
          'limitations':['Expansion after H1 193 outcome observed: not blind or independent.','Official lineup and schedule pitcher publication/receipt before game unproven; retrospective availability assumed.','256 source fixtures require date and oriented-team uniqueness; official current profiles and crosswalk do not independently prove identity. Same-team same-season player-ID crosswalk can draw on later source records for identity, not player statistics; no pregame identity claim.','No true game end or prior observation timestamps; prior calendar date completion is assumed.','Raw BF meaning unverified. Missing and stale prior data are not filled with target or later stats.','Existing frozen adapter requires separate explicit scenario support; no model fitting, tuning, predictions, ROI or operational change.']}
    out=HERE/'official-full-2026-manifest.json';tmp=HERE/'official-full-2026-manifest.json.tmp';tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,out)
    print(json.dumps({'cohort':len(original),'exclude':dict(excluded),'original_overlap':len(overlap),'new':len(complement),'latest':source_last,'after_last':target_after_last,'freshness':{k:v['overall'] for k,v in freshness.items()},'outputs':out_sha},ensure_ascii=False))

if __name__=='__main__':main()
