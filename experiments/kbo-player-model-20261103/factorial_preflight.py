"""193-game schema and leakage preflight. No predictions or label reads."""
import gzip,hashlib,importlib.util,json,subprocess,sys
from datetime import date
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_factorial_phase4',P/'phase4_predict.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
DATA_COMMIT='7a111922b406e1f86771c9ab16d0dcacfd9257fb'
STARTER=('home_starter_strength','away_starter_strength');BAT=m.LINEUP
APP_FIELDS={'bb','date','er','fixture_id','hit','hr','kk','outs','r','source_bf_raw','source_bf_units'}
FILES={'official-starter-only-2026.jsonl.gz':'beda5b82e7cd9045e01fdb09b5b27061ce2f82cd58b568b057bd119f37609fce','official-batting-and-starter-2026.jsonl.gz':'4306cc9b429446de30677a4916577ac0680480d36aa2cdd509eb487e20a0cb7a'}
CONTRACTS={'official-player-factorial-contract.json':('e03addcb','0ceef0b2417eb3ee35c01da7a4c4632441a71172def0263655d39d00065b2da7'),'official-player-full-cohort-extension.json':('69694632','5b74dcd4260b7f68cad44b6f9165a282ef712375d61adc4d519de4e03f8c3bc1')}

def exact_blob(repo,commit,path,sha):
    raw=(repo/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('source hash mismatch: '+str(path))
    blob=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+str(path).replace('\\','/')])
    if raw!=blob:raise ValueError('source does not equal committed blob: '+str(path))
    return raw

def same_except(a,b,allowed):
    return m.canonical({k:v for k,v in a.items() if k not in allowed})==m.canonical({k:v for k,v in b.items() if k not in allowed})

def validate_starter(entry,person,fixture,day,dates):
    if not person.get('player_id') or person.get('selection_basis')!='official_schedule_listed_pitcher_not_independently_confirmed_pregame':raise ValueError('invalid starter identity')
    m.adapter.check_nested(entry,fixture,day,dates)
    value=entry['value']
    if value is None:return None
    if set(value)!={'appearances','projected_player_id'} or value['projected_player_id']!=person['player_id'] or len(value['appearances'])>10:raise ValueError('starter profile mismatch')
    if any(set(a)!=APP_FIELDS for a in value['appearances']):raise ValueError('unexpected starter history fields')
    return m.adapter.scalar('home_starter_strength',value)

def validate(prior,lineup,starter,both,base):
    fid=base['fixture_id'];day=date.fromisoformat(base['event_date']);dates=validate.dates
    if any(x['fixture_id']!=fid for x in (prior,lineup,starter,both)):raise ValueError('fixture mismatch')
    if any(x['availability_tier']!='retrospective_unproven' for x in (starter,both)):raise ValueError('invalid availability')
    if any(m.canonical(x['projection'])!=m.canonical(prior['projection']) for x in (lineup,starter,both)):raise ValueError('prior metadata changed')
    if not same_except(prior['features'],lineup['features'],BAT) or not same_except(prior['features'],starter['features'],STARTER) or not same_except(lineup['features'],both['features'],STARTER) or not same_except(starter['features'],both['features'],BAT):raise ValueError('features changed outside factorial setting')
    lc=lineup['official_lineup_context']
    if m.canonical(lc)!=m.canonical(both['official_lineup_context']) or lc['official_game_id']+str(base['season'])!=fid or lc['official_game_date']!=day.strftime('%Y%m%d') or lc['basis']!=m.DATA_SCENARIO or any(lc[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('invalid lineup identity or availability')
    seen=set()
    for side in ('home','away'):
        k=side+'_lineup_strength';entry=lineup['features'][k];players=lc['lineups'][side];profiles=entry['value']
        if lc[side+'_team_id']!=m.TEAMS.get(base[side+'_id']) or len(players)!=9 or [x['order'] for x in players]!=list(range(1,10)) or len(profiles)!=9:raise ValueError('invalid lineup order or team')
        ids=[x['player_id'] for x in players]
        if len(set(ids))!=9 or any(x in seen for x in ids) or [x['player_id'] for x in profiles]!=ids:raise ValueError('invalid lineup player identity')
        seen.update(ids)
        for profile in profiles:
            if set(profile)!={'player_id','history'} or profile['history'] is not None and (len(profile['history'])>10 or any(set(h)!={'fixture_id','date','ab','hit','bb','hr'} for h in profile['history'])):raise ValueError('invalid lineup history fields')
        m.adapter.check_nested(entry,fid,day,dates)
        if m.canonical(entry)!=m.canonical(both['features'][k]):raise ValueError('lineup not identical')
    ctx=starter['official_schedule_pitcher_context']
    if m.canonical(ctx)!=m.canonical(both['official_schedule_pitcher_context']):raise ValueError('starter context mismatch')
    if ctx['basis']!='retrospective_official_schedule_listed_actual_starter_assumed_available' or any(ctx[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')) or ctx['baseline_projection_metadata_not_official_selector'] is not True:raise ValueError('unsafe availability')
    if ctx['game_date']!=day.strftime('%Y%m%d') or fid!=ctx['official_game_id']+str(base['season']):raise ValueError('starter game mismatch')
    result=[];ids=[]
    for side in ('home','away'):
        person=ctx['pitchers'][side];ids.append(person['player_id']);key=side+'_starter_strength'
        if person['team_id']!=m.TEAMS.get(base[side+'_id']) or person['source_field']!=('B_PIT_P_ID' if side=='home' else 'T_PIT_P_ID'):raise ValueError('starter team/source mismatch')
        if m.canonical(starter['features'][key])!=m.canonical(both['features'][key]):raise ValueError('starter features differ')
        result.append(validate_starter(starter['features'][key],person,fid,day,dates))
    if len(set(ids))!=2:raise ValueError('duplicate starter')
    return result

def main():
    for name,(commit,sha) in CONTRACTS.items():exact_blob(m.ORCH,commit,Path('experiments/kbo-player-challenger-20261103')/name,sha)
    inputs=m.load_inputs();base=m.index(inputs['features']);prior=m.index(inputs['prior']);lineup=m.index(inputs['overlay']);sets=[]
    for name,sha in FILES.items():
        raw=exact_blob(m.DATA,DATA_COMMIT,Path('experiments/kbo-player-data-20261103')/name,sha)
        sets.append(m.index([json.loads(x) for x in gzip.decompress(raw).splitlines()]))
    starter,both=sets
    if len(lineup)!=193 or set(lineup)!=set(starter) or set(lineup)!=set(both):raise ValueError('193 fixture cohort mismatch')
    validate.dates={fid:date.fromisoformat(x['event_date']) for fid,x in base.items()};nulls=0
    for fid in sorted(lineup):nulls+=sum(x is None for x in validate(prior[fid],lineup[fid],starter[fid],both[fid],base[fid]))
    if nulls!=20:raise ValueError('unexpected 193 starter scalar null count')
    print(json.dumps({'status':'ready_for_full_overlay_data','validated_fixtures':len(lineup),'starter_sides':len(lineup)*2,'starter_scalar_null_sides':nulls,'model_predictions':False,'outcome_labels_read':False,'source_commit':DATA_COMMIT}))
if __name__=='__main__':main()
