"""Resolve names to IDs only with official same-game evidence and official profiles."""
import base64,gzip,json,re
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
from collect import HERE,STATE,ROWS,BATTERS,EXPECTED,BATTER_SHA,PROFILE,MAX,Capture,Stop,sha,fidpath
from lineup_reference import profile_name,publication_time_fields
from daily_identity import derive as derive_daily_identity
PITCHERS=Path('C:/Users/user/Documents/Codex/kbodata/data/raw/detail/kbo_baseball_2023_2026.json')
PITCHER_SHA='c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'
PROFILE_NAMES={}
def official_profile_name(raw,meta):
    digest=meta['sha256']
    if digest not in PROFILE_NAMES:PROFILE_NAMES[digest]=profile_name(raw)
    return PROFILE_NAMES[digest]

def fetch_profile(c,pid,network):
    if pid in c.unavailable_profiles:return None
    try:return c.fetch('GET',PROFILE,{'playerId':pid},allow_network=network)
    except Stop as e:
        if str(e)=='http_404':c.unavailable_profiles.add(pid);return None
        raise

def actual_pitcher(box,one,side,pitcher_game,c,network):
    table=box['tables'][4 if side=='home' else 3]
    heads=[str(x.get('Text') or '').strip() for x in table['headers'][0]['row']];role=heads.index('등판');name_col=heads.index('선수명')
    rows=[[str(x.get('Text') or '').strip() for x in row['row']] for row in table['rows']]
    starters=[r for r in rows if len(r)>max(role,name_col) and r[role]=='선발']
    if len(starters)!=1:raise ValueError('actual_starter_not_unique')
    value=starters[0][name_col];g=one['schedule_game'];prefix='B' if side=='home' else 'T';pid=str(g.get(prefix+'_PIT_P_ID') or '');scheduled_name=str(g.get(prefix+'_PIT_P_NM') or '').strip()
    if pid.isdigit() and scheduled_name and value==pid:
        return {'player_id':pid,'name':scheduled_name,'actual_record_verified':True,'actual_record_starter_cell':value,'schedule_agrees':True,'identity_basis':'official_numeric_starter_marker_and_matching_schedule_identity'}
    if not value.isdigit():
        # A repeated official same-team name cannot identify which pitcher
        # started, even when the schedule has one of the same-name IDs.
        if sum(r[name_col]==value for r in rows if len(r)>name_col)!=1:raise ValueError('duplicate_official_pitcher_name')
        if pid.isdigit():
            answer=fetch_profile(c,pid,network)
            if answer:
                profile_raw,meta=answer
                if official_profile_name(profile_raw,meta)==value:
                    return {'player_id':pid,'name':value,'actual_record_verified':True,'actual_record_starter_cell':value,'schedule_agrees':True,'scheduled_name':scheduled_name,'name_change_or_alias':value!=scheduled_name,'identity_basis':'unique_official_named_starter_same_schedule_id_and_official_profile','profile_sha256':meta['sha256'],'profile_received_at':meta['received_at']}
    # When the actual starter differs, use the same game's pitcher archive only
    # for a name/ID candidate; require the official profile to verify the name.
    expected_date=one['date'][:4]+'-'+one['date'][4:6]+'-'+one['date'][6:]
    if not pitcher_game or (pitcher_game.get('date'),pitcher_game.get('home'),pitcher_game.get('away'))!=(expected_date,one['home_team']['name'],one['away_team']['name']):raise ValueError('changed_starter_identity_archive_unavailable')
    candidates={(str(p.get('pcode') or ''),str(p.get('name') or '').strip()) for p in pitcher_game['data'][side]}
    candidates={(i,n) for i,n in candidates if i.isdigit() and n and (i==value if value.isdigit() else True)}
    if value==scheduled_name and {(i,n) for i,n in candidates if n==value}=={(pid,scheduled_name)}:
        return {'player_id':pid,'name':scheduled_name,'actual_record_verified':True,'actual_record_starter_cell':value,'schedule_agrees':True,'identity_basis':'official_named_starter_marker_schedule_and_unique_same_game_identity'}
    matched=[];unavailable=False;checks=[]
    for i,n in sorted(candidates):
        answer=fetch_profile(c,i,network)
        if answer:
            raw,meta=answer
            official_name=official_profile_name(raw,meta);checks.append({'player_id':i,'name':official_name,'sha256':meta['sha256'],'received_at':meta['received_at']})
            if value.isdigit() or official_name==value:matched.append((i,official_name,n,meta))
        else:unavailable=True;checks.append({'player_id':i,'status':'unavailable'})
    if unavailable or len(matched)!=1:raise ValueError('changed_starter_official_identity_unavailable_or_ambiguous')
    i,official_name,archive_name,meta=matched[0]
    return {'player_id':i,'name':official_name,'actual_record_verified':True,'actual_record_starter_cell':value,'schedule_agrees':i==pid,'scheduled_name':scheduled_name,'archive_name':archive_name,'name_change_or_alias':len({official_name,archive_name,scheduled_name}-{''})>1,'identity_basis':'official_starter_marker_same_game_archive_candidate_and_official_profile','profile_sha256':meta['sha256'],'profile_received_at':meta['received_at'],'candidate_profile_checks':checks}

def box_names(box,side):
    table=box['tables'][2 if side=='home' else 1];out=set()
    for row in table.get('rows',[]):
        cells=[str(x.get('Text') or '').strip() for x in row.get('row',[])]
        if len(cells)>=3 and cells[0].isdigit():out.add((int(cells[0]),cells[2]))
    return out

def build(fetch_missing_profiles=False):
    rows_bytes=ROWS.read_bytes();batters_bytes=BATTERS.read_bytes();pitchers_bytes=PITCHERS.read_bytes()
    if sha(rows_bytes)!=EXPECTED or sha(batters_bytes)!=BATTER_SHA or sha(pitchers_bytes)!=PITCHER_SHA:raise ValueError('fixed input changed')
    contract_sha=sha((HERE/'contract.json').read_bytes())
    if contract_sha!='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480':raise ValueError('comparison contract changed')
    daily=derive_daily_identity()
    (HERE/'pitcher-daily-identity.json').write_bytes((json.dumps(daily,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode())
    rows=[r for r in map(json.loads,rows_bytes.splitlines()) if r['season']==2025];archive=json.loads(batters_bytes);pitcher_archive=json.loads(pitchers_bytes)
    identity=[]
    for source in rows:
        fid=source['fixture_id'];stage_path=fidpath('stage1',fid);one=json.loads(stage_path.read_bytes()) if stage_path.exists() else {};gid=one.get('official_game_id')
        item={'fixture_id':fid,'official_game_id':gid,'date':one.get('date'),'home_team':one.get('home_team'),'away_team':one.get('away_team'),'batters':None,'pitchers':None}
        if gid:
            expected_day=one['date'][:4]+'-'+one['date'][4:6]+'-'+one['date'][6:];expected=(expected_day,one['home_team']['name'],one['away_team']['name'])
            for kind,source_archive in [('batters',archive),('pitchers',pitcher_archive)]:
                game=source_archive.get(fid) or source_archive.get(gid+'2025')
                if not game or (game.get('date'),game.get('home'),game.get('away'))!=expected:continue
                if kind=='batters':item[kind]={side:[{'slot':p.get('order'),'player_id':str(p.get('code') or ''),'name':str(p.get('name') or '').strip()} for p in game['data'][side]] for side in ('home','away')}
                else:item[kind]={side:[{'player_id':str(p.get('pcode') or ''),'name':str(p.get('name') or '').strip()} for p in game['data'][side]] for side in ('home','away')}
        identity.append(item)
    identity_raw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in identity).encode();identity_file=HERE/'identity-candidates-2025.jsonl.gz';identity_file.write_bytes(gzip.compress(identity_raw,mtime=0))
    c=Capture({'phase':'profiles','denominator':736});c.unavailable_profiles=set();records=[];counts=Counter();stop=None
    for i,row in enumerate(rows):
        fid=row['fixture_id'];p1=fidpath('stage1',fid);p2=fidpath('stage2',fid)
        out={'fixture_id':fid,'season':2025,'date':None,'official_game_id':None,'home':None,'away':None,
             'source_kind':'retrospective_official_confirmed_lineup_and_same_game_identity',
             'historical_publication_time_proven':False,'historical_receipt_proven':False,
             'eligible_as_pregame_feature':False,'exclusion_reasons':[]}
        if not p1.exists():out['exclusion_reasons'].append('lineup_not_processed');records.append(out);continue
        one=json.loads(p1.read_bytes());day=one.get('date');out['date']=(day[:4]+'-'+day[4:6]+'-'+day[6:]) if day else None;out['official_game_id']=one.get('official_game_id')
        if not one.get('schedule_game'):
            out['exclusion_reasons']+=one.get('exclusion_reasons',['stage1_unresolved']);records.append(out);continue
        out['schedule_evidence']=one['schedule_evidence'];out['lineup_evidence']=one['lineup_evidence'];out['lineup_confirmed']=one['lineup_flag']
        out['lineup_status']='official_confirmed_flag_true' if one['lineup_flag'] else 'unconfirmed_recent_or_default'
        out['archived_kickoff_at']=one.get('archived_kickoff_at',row.get('kickoff_at'));out['source_kickoff_matches_official']=one.get('source_kickoff_matches_official')
        if out['source_kickoff_matches_official'] is None and row.get('kickoff_at'):
            out['source_kickoff_matches_official']=datetime.fromisoformat(row['kickoff_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d %H:%M')==one['date']+' '+one['schedule_game']['G_TM']
        out['fixture_match_basis']=one.get('fixture_match_basis') or ('exact_official_game_id' if re.fullmatch(r'\d{8}[A-Z]{4}\d\d{4}',fid) else 'unique_date_home_away_schedule_match')
        if re.fullmatch(r'\d{8}[A-Z]{4}\d\d{4}',fid) and not fid.startswith('2025'):
            if fid[4:13]!=one['official_game_id'][4:] or fid[4:8]!=one['date'][4:8]:raise ValueError('sentinel_fixture_alias_evidence_mismatch')
            out['fixture_match_basis']='encoded_year_alias_exact_official_schedule_match'
        out['source_publication_time_fields']=publication_time_fields(gzip.decompress((STATE/one['lineup_evidence']['raw_file']).read_bytes()))
        two=json.loads(p2.read_bytes()) if p2.exists() else {'exclusion_reasons':['official_box_not_processed']}
        out['exclusion_reasons']+=one.get('exclusion_reasons',[])
        if not two.get('box_evidence'):out['exclusion_reasons']+=two.get('exclusion_reasons',[])
        if two.get('box_evidence'):out['box_evidence']=two['box_evidence'];box=json.loads(gzip.decompress((STATE/two['box_evidence']['raw_file']).read_bytes()))
        else:box=None
        game=archive.get(fid) or archive.get(one['official_game_id']+'2025')
        archive_ok=bool(game and game.get('date')==one['date'][:4]+'-'+one['date'][4:6]+'-'+one['date'][6:] and game.get('home')==one['home_team']['name'] and game.get('away')==one['away_team']['name'])
        for side in ('home','away'):
            prefix='B' if side=='home' else 'T';game_fields=one['schedule_game']
            obj={'team':one[side+'_team'],'ordered_hitters':[],'starter':None,
                 'scheduled_starter':{'player_id':str(game_fields.get(prefix+'_PIT_P_ID') or ''),'name':str(game_fields.get(prefix+'_PIT_P_NM') or '').strip(),'actual_starter_verified':False}}
            if side=='away' and fid in daily:
                obj['starter']=daily[fid]
            elif box:
                try:obj['starter']=actual_pitcher(box,one,side,pitcher_archive.get(fid) or pitcher_archive.get(one['official_game_id']+'2025'),c,fetch_missing_profiles and not stop)
                except (ValueError,KeyError,TypeError,IndexError,UnicodeError) as e:obj['starter_error']=str(e)[:160]
                except Stop as e:stop=str(e);obj['starter_error']='official_profile_unavailable'
            if one['lineup_flag']:
                official=box_names(box,side) if box else set()
                for hitter in one['lineups'][side]:
                    entry={'slot':hitter['order'],'name':hitter['name'],'player_id':None,'position':hitter['position'],
                           'evidence':{'lineup_sha256':one['lineup_evidence']['sha256'],'lineup_received_at':one['lineup_evidence']['received_at']},'exclusion_reasons':[]}
                    if not box:entry['exclusion_reasons'].append('official_same_game_box_unavailable')
                    elif (hitter['order'],hitter['name']) not in official:entry['exclusion_reasons'].append('official_same_game_name_slot_unmatched')
                    elif not archive_ok:entry['exclusion_reasons'].append('identity_archive_same_game_missing_or_mismatch')
                    else:
                        same_slot=[x for x in game['data'][side] if x.get('order')==hitter['order'] and str(x.get('code','')).isdigit()]
                        # Test all same-game, same-slot candidates, including
                        # abbreviations and substitutions, against the official
                        # profile so a second exact name cannot be overlooked.
                        ids=sorted({str(x['code']) for x in same_slot})
                        entry['evidence']['candidate_player_ids']=ids
                        if not ids:entry['exclusion_reasons'].append('identity_candidate_missing')
                        else:
                            matched=[];not_available=False;profile_checks=[]
                            for pid in ids:
                                try:answer=fetch_profile(c,pid,fetch_missing_profiles and not stop)
                                except Stop as e:stop=str(e);answer=None
                                if answer is None:not_available=True;profile_checks.append({'player_id':pid,'status':'unavailable'});continue
                                raw,meta=answer
                                try:name=official_profile_name(raw,meta)
                                except (ValueError,UnicodeError):not_available=True;profile_checks.append({'player_id':pid,'status':'invalid_profile','sha256':meta['sha256']});continue
                                profile_checks.append({'player_id':pid,'name':name,'sha256':meta['sha256'],'received_at':meta['received_at']})
                                if name==hitter['name']:matched.append((pid,meta))
                            entry['evidence']['candidate_profile_checks']=profile_checks
                            if not_available:entry['exclusion_reasons'].append('official_profile_unavailable')
                            elif len(matched)!=1:entry['exclusion_reasons'].append('official_profile_identity_not_unique')
                            else:
                                entry['player_id']=matched[0][0];entry['evidence'].update(box_sha256=two['box_evidence']['sha256'],box_received_at=two['box_evidence']['received_at'],profile_sha256=matched[0][1]['sha256'],profile_received_at=matched[0][1]['received_at'],profile_player_id=matched[0][0],identity_archive_sha256=BATTER_SHA,identity_basis='same_game_team_name_slot_archive_candidate_official_box_and_official_profile')
                    obj['ordered_hitters'].append(entry)
            if obj['starter']:
                obj['starter']['evidence']={'schedule_sha256':one['schedule_evidence']['sha256'],'box_sha256':two['box_evidence']['sha256'],'box_received_at':two['box_evidence']['received_at']}
            else:out['exclusion_reasons'].append(side+'_actual_starter_unverified')
            out[side]=obj
        assigned=[p['player_id'] for side in ('home','away') for p in out[side]['ordered_hitters'] if p['player_id']]
        if len(set(assigned))!=len(assigned):
            for side in ('home','away'):
                for p in out[side]['ordered_hitters']:
                    if p['player_id'] and assigned.count(p['player_id'])>1:p['player_id']=None;p['exclusion_reasons'].append('duplicate_player_id')
        out['complete_verified']=bool(one['lineup_flag'] and all(out[s]['starter'] and len(out[s]['ordered_hitters'])==9 and all(p['player_id'] for p in out[s]['ordered_hitters']) for s in ('home','away')))
        out['batting_roster_semantics']='official_confirmed_lineup_with_same_game_appearance_identity; post_announcement_starting_change_unverified'
        out['actual_batting_starter_changes_verified']=False
        if not out['complete_verified']:out['exclusion_reasons'].append('complete_verified_lineup_unavailable')
        records.append(out);counts['complete_verified']+=int(out['complete_verified']);counts['official_lineup_confirmed']+=int(one['lineup_flag']);counts['both_actual_starters']+=int(all(out[s]['starter'] for s in ('home','away')))
        if fetch_missing_profiles:c.update(phase='profiles',fixture_index=i+1,completed=i+1,profile_stop_reason=stop)
    raw=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in records).encode();(HERE/'actual-lineups-2025.jsonl.gz').write_bytes(gzip.compress(raw,mtime=0))
    game_reasons=Counter(reason for record in records for reason in record['exclusion_reasons'])
    player_reasons=Counter(reason for record in records for side in ('home','away') if isinstance(record.get(side),dict) for player in record[side]['ordered_hitters'] for reason in player['exclusion_reasons'])
    counts['source_kickoff_mismatches']=sum(record.get('source_kickoff_matches_official') is False for record in records)
    counts['encoded_year_alias_verified']=sum(record.get('fixture_match_basis')=='encoded_year_alias_exact_official_schedule_match' for record in records)
    counts['changed_scheduled_starters_verified']=sum(bool(record[side].get('starter') and not record[side]['starter'].get('schedule_agrees')) for record in records for side in ('home','away') if isinstance(record.get(side),dict))
    counts['starter_name_change_or_alias_verified']=sum(bool(record[side].get('starter') and record[side]['starter'].get('name_change_or_alias')) for record in records for side in ('home','away') if isinstance(record.get(side),dict))
    evidence=HERE/'raw-evidence.jsonl.gz'
    with evidence.open('wb') as rawfile:
        with gzip.GzipFile(fileobj=rawfile,mode='wb',mtime=0) as stream:
            for meta in c.records():
                item=dict(meta)
                if meta.get('raw_file'):
                    body=gzip.decompress((STATE/meta['raw_file']).read_bytes())
                    if sha(body)!=meta['sha256']:raise ValueError('raw evidence sha mismatch')
                    item['body_base64']=base64.b64encode(body).decode()
                stream.write((json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())
    manifest={'status':'ready_for_join' if len(records)==736 and stop is None and all(fidpath('stage1',r['fixture_id']).exists() and fidpath('stage2',r['fixture_id']).exists() for r in rows) else 'partial_unprocessed_or_profile_stop',
      'denominator':736,'stage1_processed':sum(fidpath('stage1',r['fixture_id']).exists() for r in rows),'stage2_processed':sum(fidpath('stage2',r['fixture_id']).exists() for r in rows),'counts':dict(counts),'game_exclusion_reasons':dict(game_reasons),'player_exclusion_reasons':dict(player_reasons),'new_requests':c.count,'request_cap':MAX,'profile_stop_reason':stop,
      'daily_disambiguated_pitchers':len(daily),'daily_identity_sha256':sha((HERE/'pitcher-daily-identity.json').read_bytes()),
      'unconfirmed_review_sha256':sha((HERE/'unconfirmed-review.json').read_bytes()),
      'source_rows_sha256':EXPECTED,'contract_sha256':contract_sha,'identity_archive_sha256':BATTER_SHA,'pitcher_identity_archive_sha256':PITCHER_SHA,'identity_candidates_gzip_sha256':sha(identity_file.read_bytes()),'identity_candidates_uncompressed_sha256':sha(identity_raw),'lineups_gzip_sha256':sha((HERE/'actual-lineups-2025.jsonl.gz').read_bytes()),'lineups_uncompressed_sha256':sha(raw),'evidence_gzip_sha256':sha(evidence.read_bytes()),
      'complete_verified_definition':'official confirmed batting identities plus actual pitcher markers; post-announcement batting changes are not established',
      'historical_receipt_proven':False,'historical_publication_time_proven':False,'target_game_statistics_used_as_features':False}
    (HERE/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode());print(json.dumps(manifest,ensure_ascii=False),flush=True)

if __name__=='__main__':
    import sys
    build(fetch_missing_profiles='--fetch-profiles' in sys.argv)
