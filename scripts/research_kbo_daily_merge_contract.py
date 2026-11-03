"""Explicit, detached v1 + v2 join; target-day statistics are never returned."""
from datetime import datetime
import gzip,hashlib,json,re
from pathlib import Path

V1_SHA='e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334'
DAILY='https://www.koreabaseball.com/Record/Player/HitterDetail/Daily.aspx'
PITCHER='https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx'


def load_verified_sources(v1_path,supplement_path,expected_supplement_sha):
    v=Path(v1_path).read_bytes();s=Path(supplement_path).read_bytes()
    if hashlib.sha256(v).hexdigest()!=V1_SHA or hashlib.sha256(s).hexdigest()!=expected_supplement_sha:
        raise ValueError('input_sha_mismatch')
    return ([json.loads(x) for x in gzip.decompress(v).splitlines() if x.strip()],
            [json.loads(x) for x in gzip.decompress(s).splitlines() if x.strip()])


def prior_batter_history(v1_records,supplement_records,target_date):
    if not re.fullmatch(r'\d{8}',target_date):raise ValueError('invalid_target_date')
    try:datetime.strptime(target_date,'%Y%m%d')
    except ValueError as exc:raise ValueError('invalid_target_date') from exc
    sources={x['official_game_id']:x for x in v1_records};supps={x['official_game_id']:x for x in supplement_records}
    if len(sources)!=len(v1_records) or len(supps)!=len(supplement_records):raise ValueError('duplicate_game_id')
    if set(supps)-set(sources):raise ValueError('supplement_game_missing_in_v1')
    history=[];eligible=missing=zero_opportunities=0
    for gid,addition in supps.items():
        original=sources[gid]
        if addition.get('source_v1_gzip_sha256')!=V1_SHA or addition.get('game_date')!=original.get('game_date') or addition.get('fixture_id')!=original.get('fixture_id'):
            raise ValueError('source_or_game_mismatch')
        day=original['game_date']
        if day>=target_date or original.get('completed') is not True or original.get('status')!='ok':continue
        for side in ('home','away'):
            rows=addition['batters'][side];prior=original['statistics']['batters'][side]
            if len(rows)!=len(prior):raise ValueError('batter_row_count_mismatch')
            for index,(a,p) in enumerate(zip(rows,prior)):
                if a.get('v1_row_index')!=index or a.get('name')!=p.get('name') or a.get('player_id')!=p.get('player_id') or a.get('official_v1')!={k:p[k] for k in ('AB','H','R','RBI')}:
                    raise ValueError('batter_identity_or_numbers_mismatch')
                eligible+=1
                if a.get('status')!='matched' or not a.get('daily_evidence') or not p.get('player_id'):
                    missing+=1;continue
                ev=a['daily_evidence']
                if ev.get('url')!=DAILY or ev.get('http_status')!=200 or str((ev.get('query') or {}).get('playerId'))!=str(p['player_id']) or not ev.get('sha256'):
                    raise ValueError('daily_evidence_mismatch')
                if any(type(v) is not int or v<0 for v in (p.get('AB'),p.get('H'),a.get('BB'))):
                    raise ValueError('invalid_lagged_numeric')
                if p['H']>p['AB']:raise ValueError('invalid_lagged_numeric')
                if p['AB']+a['BB']==0:
                    zero_opportunities+=1;continue
                history.append({'official_game_id':gid,'game_date':day,'side':side,'team_id':original['team_ids'][side],
                    'player_id':p['player_id'],'AB':p['AB'],'H':p['H'],'BB':a['BB'],
                    'historical_receipt_proven':False,'availability_basis':'retrospective_completed_by_next_day_assumption',
                    'BB_source':a['daily_evidence']})
    return {'history':history,'coverage':{'prior_batter_rows':eligible,'included':len(history),'excluded_missing_or_conflict':missing,
                                          'excluded_zero_opportunities':zero_opportunities}}


def verified_pitcher_ids(v1_game,supplement_game):
    if supplement_game.get('source_v1_gzip_sha256')!=V1_SHA or supplement_game.get('official_game_id')!=v1_game.get('official_game_id') or supplement_game.get('game_date')!=v1_game.get('game_date'):
        raise ValueError('source_or_game_mismatch')
    result=[]
    for side in ('home','away'):
        original=v1_game['statistics']['pitchers'][side];added=supplement_game['pitcher_id_supplement'][side]
        if len(original)!=len(added):raise ValueError('pitcher_row_count_mismatch')
        for index,(p,a) in enumerate(zip(original,added)):
            if a.get('v1_row_index')!=index or a.get('name')!=p['name'] or a.get('original_player_id')!=p['player_id'] or a.get('source_numeric_id_claim')!=p.get('source_numeric_id_claim'):
                raise ValueError('pitcher_identity_mismatch')
            if a.get('status')=='verified_official_profile':
                ev=a.get('profile_evidence') or {}
                if p['player_id'] is not None or a.get('supplement_player_id')!=p.get('source_numeric_id_claim') or ev.get('url')!=PITCHER or ev.get('http_status')!=200 or str((ev.get('query') or {}).get('playerId'))!=str(a.get('supplement_player_id')) or not ev.get('sha256'):
                    raise ValueError('invalid_profile_link')
                result.append({'side':side,'v1_row_index':index,'player_id':a['supplement_player_id'],'profile_evidence':a['profile_evidence']})
    return result
