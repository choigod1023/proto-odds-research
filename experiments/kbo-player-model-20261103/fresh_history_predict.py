"""Create fresh-history fixed-model predictions using immutable source Git blobs."""
import argparse,copy,gzip,hashlib,importlib.util,json,subprocess,sys
from datetime import date
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_fresh_factorial_pred',P/'factorial_predict.py');oldp=importlib.util.module_from_spec(spec);sys.modules[spec.name]=oldp;spec.loader.exec_module(oldp);f=oldp.f;m=f.m
DATA_COMMIT='0d980b40697997021256be9f42f6fd254b3c1049';MANIFEST_SHA='5b0ea2b96787f04189330d6f3ecc55a18e846400d7afde93cc857b64fe8483e3';SOURCE_COMMIT='d11367b7b37f3ea34f4c08987ca042af9235575e'
OLD_SHA='669a0016f07275ed9136aa55fdf9f17c96f2c497a32d323a3cad9a2a0dcbab17';OLD_COMMIT='29b3ebe623b2260d28139ebe3ef422ed442cfacf'
HASH={'projected':'8b090ed9b204a50584c5e2a854bd0c6210a133a8518de78db51ca8f4217aa23f','official_batting_only':'8e8bc1850c18af2e258a26805e3090abce621264adf15b0b6d1d748ab57a8e06','official_starter_only':'65b4cad44b30e8185760dbabbcaebe4dd26f29dffa3ac7fe57dd7e05cc05c757','official_batting_and_starter':'82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706','incomplete_batters':'9132f681b25a8bb413fe03f1d4dfdb8a58b046f0ec9f422278c2c7d4806a2ef4'}
PLAYER=tuple(side+'_'+part for side in ('home','away') for part in ('lineup_strength','starter_strength','bullpen_load'));NAMES={'projected':'original_projected','official_batting_only':'official_batting_only','official_starter_only':'official_starter_only','official_batting_and_starter':'official_batting_and_starter'}
def blob(repo,commit,path,sha):
    raw=subprocess.check_output(['git','-C',str(repo),'show',commit+':'+str(path).replace('\\','/')])
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Git blob SHA mismatch: '+str(path))
    return raw

def load():
    blob(m.ORCH,'497cf651205fdda83fd03504d1b5d58c36c6ac9c',Path('experiments/kbo-player-challenger-20261103/fresh-history-comparison-contract.json'),'781ac8cea06ef5a645e8b3075208083f5a460c622c329fba6f05be10c76cc903')
    data=Path('experiments/kbo-player-data-20261103');manifest=json.loads(blob(m.DATA,DATA_COMMIT,data/'fresh-history-2026-manifest.json',MANIFEST_SHA))
    if manifest['source_commit']!=SOURCE_COMMIT or manifest['outputs_sha256']!=HASH or manifest['point_in_time_verified']!=0 or manifest['cohorts']!={'all':709,'excluded':17,'new_516':516,'original_193':193}:raise ValueError('manifest contract mismatch')
    src={}
    for name,sha in HASH.items():
        raw=blob(m.DATA,DATA_COMMIT,data/('fresh-history-2026-'+name.replace('_batters','-batters')+'.jsonl.gz'),sha)
        if name!='incomplete_batters':src[name]=m.index([json.loads(x) for x in gzip.decompress(raw).splitlines()])
    old=json.loads(blob(m.ROOT,OLD_COMMIT,Path('experiments/kbo-player-model-20261103/factorial/predictions.json'),OLD_SHA))
    # Existing features and model are checked by the original immutable phase 4 loader.
    x=m.load_inputs()
    return x,src,old,manifest

def validate_entry(key,entry,fid,day,dates):
    if entry['availability_basis']!='prior_calendar_day_assumed_completed_not_received' or entry['received_at'] is not None or entry['completed_at'] is not None:raise ValueError('unexpected history availability')
    m.adapter.check_nested(entry,fid,day,dates);v=entry['value']
    if v is None:return None
    if 'lineup' in key:
        if len(v)!=9 or len({p['player_id'] for p in v})!=9 or any(set(p)!={'player_id','history'} or not p['player_id'] for p in v):raise ValueError('invalid selected lineup')
        for p in v:
            h=p['history']
            if h is not None and (len(h)>10 or any(not {'fixture_id','date','ab','hit','bb','hr'}<=set(z) or set(z)-{'fixture_id','date','ab','hit','bb','hr','provenance'} or not m.adapter.nonnegative(z['ab'],z['hit'],z['bb']) or z['hit']>z['ab'] for z in h)):raise ValueError('incomplete batter record')
    elif 'starter' in key:
        if set(v)!={'projected_player_id','appearances'} or not v['projected_player_id'] or len(v['appearances'])>10 or any(not {'fixture_id','date','er','outs','r'}<=set(z) or set(z)-{'fixture_id','date','er','outs','r','provenance'} or not m.adapter.nonnegative(z['er'],z['outs']) for z in v['appearances']):raise ValueError('invalid starter history')
    else:
        if set(v)!={'games','kind','schedule_coverage_verified'} or type(v['schedule_coverage_verified']) is not bool or any(not m.adapter.nonnegative(z['relief']['outs']) for z in v['games']):raise ValueError('invalid bullpen outs')
    return m.adapter.scalar(key,v)

def build(x,src,old,manifest):
    base=m.index(x['features']);prior=m.index(x['prior']);oldrows={v['fixture_id']:v for v in old['predictions']};ids=set(oldrows)
    if len(ids)!=709 or any(set(z)!=ids for z in src.values()):raise ValueError('source fixture mismatch')
    dates={fid:date.fromisoformat(r['event_date']) for fid,r in base.items()};ordered=sorted(ids,key=lambda fid:(base[fid]['event_date'],fid));rows={name:[] for name in NAMES.values()};nulls={name:{k:0 for k in PLAYER} for name in NAMES.values()}
    for fid in ordered:
        day=dates[fid];projected=src['projected'][fid]
        for name in NAMES:
            item=src[name][fid]
            if item['fixture_id']!=fid or item['scenario']!='fresh_history_'+name or item['availability_tier']!='retrospective_unproven' or item['completed_at'] is not None or item['received_at'] is not None:raise ValueError('bad scenario or availability')
            if m.canonical(item['projection'])!=m.canonical(projected['projection']):raise ValueError('projection metadata mismatch')
            if not f.same_except(item['features'],prior[fid]['features'],PLAYER):raise ValueError('nonplayer feature changed')
            changed=() if name=='projected' else f.BAT if name=='official_batting_only' else f.STARTER if name=='official_starter_only' else f.BAT+f.STARTER
            if not f.same_except(item['features'],projected['features'],changed):raise ValueError('factorial inputs differ unexpectedly')
            m.adapter.check_nested(item['projection'],fid,day,dates)
            row=copy.deepcopy(base[fid])
            for key in PLAYER:
                entry=item['features'][key];value=validate_entry(key,entry,fid,day,dates);nulls[NAMES[name]][key]+=int(value is None);row['features'][key]={'value':value,'available_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received','source_id':m.adapter.SOURCE[key],'history_fixture_ids':entry['source_fixture_ids'],'history_latest_date':entry['latest_source_date'],'history_max_completed_at':None}
            for side in ('home','away'):
                line=item['features'][side+'_lineup_strength']['value'];pitch=item['features'][side+'_starter_strength']['value'];projection=item['projection']
                if 'batting' in name:
                    c=item['official_lineup_context'];players=c['lineups'][side];expected=[p['player_id'] for p in players]
                    if c['official_game_date']!=day.strftime('%Y%m%d') or not f.fixture_matches(fid,c['official_game_id'],base[fid]) or c[side+'_team_id']!=m.TEAMS.get(base[fid][side+'_id']) or any(c[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')) or [p['order'] for p in players]!=list(range(1,10)) or line is None or [p['player_id'] for p in line]!=expected:raise ValueError('official lineup identity mismatch')
                elif line is not None and [p['player_id'] for p in line]!=projection[side+'_lineup']['player_ids']:raise ValueError('projected lineup identity mismatch')
                if 'starter' in name:
                    c=item['official_schedule_pitcher_context'];expected=c['pitchers'][side]['player_id']
                    if c['game_date']!=day.strftime('%Y%m%d') or not f.fixture_matches(fid,c['official_game_id'],base[fid]) or c['pitchers'][side]['team_id']!=m.TEAMS.get(base[fid][side+'_id']) or any(c[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('official starter identity mismatch')
                else:expected=projection[side+'_starter']['player_id']
                if pitch is not None and pitch['projected_player_id']!=expected:raise ValueError('pitcher profile identity mismatch')
            rows[NAMES[name]].append(row)
    saved=x['predictions']['models']['player'];m.model.fit=m.forbid_training;m.model.minimize=m.forbid_training
    model=m.model.ScoreModel('player',tuple(saved['teams']),np.array(saved['mean']),np.array(saved['scale']),np.array(saved['beta']),saved['alpha'],True)
    if model.alpha!=100.:raise ValueError('unexpected alpha')
    preds={k:m.model.predict(model,v) for k,v in rows.items()};out=[]
    for i,fid in enumerate(ordered):out.append({'fixture_id':fid,'event_date':base[fid]['event_date'],'cohort':oldrows[fid]['cohort'],'settings':{k:v[i] for k,v in preds.items()}})
    return {'scenario':'retrospective_source_repair_fixed_model_not_pit','historical_receipt_verified':False,'eligible_pregame':False,'data_commit':DATA_COMMIT,'source_commit':SOURCE_COMMIT,'manifest_sha256':MANIFEST_SHA,'input_sha256':HASH,'old_prediction_sha256':OLD_SHA,'counts':manifest['cohorts'],'null_feature_counts':nulls,'source_diagnostics':{'counts':manifest['counts'],'selection_counts':manifest['selection_counts'],'freshness':manifest['freshness']},'predictions':out}

def main():
    p=argparse.ArgumentParser();p.add_argument('--assume-retrospective-availability',action='store_true');p.add_argument('--output',required=True);args=p.parse_args()
    if not args.assume_retrospective_availability:raise ValueError('explicit retrospective assumption required')
    output=build(*load());path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8',newline='\n') as fp:json.dump(output,fp,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);fp.write('\n')
    print(json.dumps({'counts':output['counts'],'null_feature_counts':output['null_feature_counts']}))
if __name__=='__main__':main()
