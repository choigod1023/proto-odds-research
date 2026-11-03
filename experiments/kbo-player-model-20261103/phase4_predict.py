"""Frozen player model predictions under explicit retrospective batting order."""
import argparse,copy,gzip,hashlib,importlib.util,json,subprocess,sys
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
DATA=Path('C:/Users/user/Documents/Codex/kbodata');D=DATA/'experiments/kbo-player-data-20261103'
ORCH=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/kbo-player-orchestrator-20261103')
CONTRACT=ORCH/'experiments/kbo-player-challenger-20261103/official-lineup-comparison-contract.json'
OFFICIAL=Path('C:/Users/user/Documents/Codex/klu/.research-state/kbo-lineups-v4/official-lineups.jsonl')
DATA_COMMIT='a28cf0d19003bc436d174bb85af46be86ee1ee24';CONTRACT_COMMIT='32d22cef'
HASH={'contract':'c190df6c0f8f8f515dc2214dd59c963719b0bf81c9fa09c74ed5fbc113e351f5','overlay':'ddc66e073ada98ef9cbf469fac64aed53e45ac3d4e44a1e4330f2c661eced627','prior':'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae','official':'92015aa1af2e23813b0b4d8c3c5bd182d1c0ee7b2226920af3445e86569ae67c','features':'346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42','predictions':'d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'}
TEAMS={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}
SCENARIO='retrospective_official_batting_order_assumed_available';DATA_SCENARIO='retrospective_official_confirmed_lineup_assumed_available';LINEUP=('home_lineup_strength','away_lineup_strength')

def digest(raw):return hashlib.sha256(raw).hexdigest()
def read_checked(path,expected):
    raw=Path(path).read_bytes()
    if digest(raw)!=expected:raise ValueError('source SHA mismatch: '+str(path))
    return raw

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);o=importlib.util.module_from_spec(spec);sys.modules[name]=o;spec.loader.exec_module(o);return o
model=module('_kbo_phase4_frozen_model',P/'model.py');adapter=module('_kbo_phase4_prior_adapter',P/'adapter.py')

def forbid_training(*args,**kwargs):raise AssertionError('fit/minimize forbidden in frozen counterfactual')

def load_inputs():
    contract_raw=read_checked(CONTRACT,HASH['contract'])
    blob=subprocess.check_output(['git','-C',str(ORCH),'show',CONTRACT_COMMIT+':experiments/kbo-player-challenger-20261103/official-lineup-comparison-contract.json'])
    if blob!=contract_raw:raise ValueError('contract Git blob differs')
    contract=json.loads(contract_raw)
    if contract['scenario']!=SCENARIO or contract['input']['sha256']!=HASH['official']:raise ValueError('contract mismatch')
    official=read_checked(OFFICIAL,HASH['official'])
    inputs={}
    for k,name in [('overlay','official-lineup-overlay-2026.jsonl.gz'),('prior','prior-projected-features.jsonl.gz')]:
        raw=read_checked(D/name,HASH[k]);blob=subprocess.check_output(['git','-C',str(DATA),'show',DATA_COMMIT+':experiments/kbo-player-data-20261103/'+name])
        if raw!=blob:raise ValueError('data Git blob differs: '+name)
        inputs[k]=[json.loads(x) for x in gzip.decompress(raw).splitlines()]
    raw=read_checked(P/'phase2/features.jsonl.gz',HASH['features']);inputs['features']=[json.loads(x) for x in gzip.decompress(raw).splitlines()]
    inputs['predictions']=json.loads(read_checked(P/'phase2/predictions.json',HASH['predictions']))
    if digest((P/'model.py').read_bytes())!=inputs['predictions']['code_sha256']['model']:raise ValueError('frozen model source bytes differ')
    return inputs

def index(rows):
    out={}
    for r in rows:
        fid=r['fixture_id']
        if fid in out:raise ValueError('duplicate fixture')
        out[fid]=r
    return out

def canonical(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')

def build(rows):
    base=index(rows['features']);prior=index(rows['prior']);overlay=index(rows['overlay']);stored=rows['predictions']
    if len(base)!=2913 or len(prior)!=2913 or set(base)!=set(prior) or len(overlay)!=193:raise ValueError('unexpected source cohort')
    dates={fid:__import__('datetime').date.fromisoformat(r['event_date']) for fid,r in base.items()}
    original={p['fixture_id']:p for p in stored['predictions']['player']};team={p['fixture_id']:p for p in stored['predictions']['team']}
    if len(original)!=726 or set(original)!=set(team) or not set(overlay)<=set(original):raise ValueError('frozen prediction mismatch')
    saved=stored['models']['player'];model.fit=forbid_training;model.minimize=forbid_training
    estimator=model.ScoreModel('player',tuple(saved['teams']),np.array(saved['mean']),np.array(saved['scale']),np.array(saved['beta']),saved['alpha'],True)
    if saved['alpha']!=100.0:raise ValueError('unexpected frozen alpha')
    ordered=sorted(overlay,key=lambda fid:(base[fid]['event_date'],fid));oldrows=[];newrows=[];missing_players=0;null_lineups=0;nonlineup_hash=hashlib.sha256()
    for fid in ordered:
        src=overlay[fid];old=prior[fid];row=base[fid];context=src['official_lineup_context']
        if row['season']!=2026 or row['availability_tier']!='retrospective_unproven' or src['availability_tier']!='retrospective_unproven' or src['scenario']!=DATA_SCENARIO:raise ValueError('invalid target tier/scenario')
        if context['basis']!=DATA_SCENARIO or any(context[k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')) or context['baseline_projection_metadata_not_official_selector'] is not True:raise ValueError('unsafe availability promotion')
        if context['official_game_date']!=row['event_date'].replace('-',''):raise ValueError('official date mismatch')
        if context['home_team_id']!=TEAMS.get(row['home_id']) or context['away_team_id']!=TEAMS.get(row['away_id']):raise ValueError('official oriented teams mismatch')
        if canonical(src['projection'])!=canonical(old['projection']):raise ValueError('prior projection changed')
        old_non={k:v for k,v in old['features'].items() if k not in LINEUP};new_non={k:v for k,v in src['features'].items() if k not in LINEUP}
        if canonical(old_non)!=canonical(new_non):raise ValueError('nonlineup original data changed')
        nonlineup_hash.update(canonical({'fixture_id':fid,'features':new_non})+b'\n')
        updated=copy.deepcopy(row);seen=set()
        for side,name in [('home',LINEUP[0]),('away',LINEUP[1])]:
            entry=src['features'][name];players=context['lineups'][side];profiles=entry['value']
            if len(players)!=9 or [v['order'] for v in players]!=list(range(1,10)) or len(profiles)!=9:raise ValueError('incomplete official order')
            ids=[v['player_id'] for v in players]
            if len(set(ids))!=9 or any(x in seen for x in ids) or [p['player_id'] for p in profiles]!=ids:raise ValueError('duplicate/incorrect official player identity')
            seen.update(ids)
            for p in profiles:
                if set(p)!={'player_id','history'}:raise ValueError('unexpected target player data')
                history=p['history'];missing_players+=int(history is None)
                if history is not None and (len(history)>10 or any(set(h)!={'fixture_id','date','ab','hit','bb','hr'} for h in history)):raise ValueError('unexpected historical stat fields')
            adapter.check_nested(entry,fid,dates[fid],dates)
            val=adapter.scalar(name,profiles);null_lineups+=int(val is None)
            updated['features'][name]={'value':val,'available_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received','source_id':'batters','history_fixture_ids':entry['source_fixture_ids'],'history_latest_date':entry['latest_source_date'],'history_max_completed_at':None}
        if any(updated[k]!=row[k] for k in row if k!='features') or any(updated['features'].get(k)!=row['features'].get(k) for k in row['features'] if k not in LINEUP):raise ValueError('nonlineup model inputs changed')
        oldrows.append(row);newrows.append(updated)
    oldraw,oldmeta,_=model._raw(oldrows,'player',estimator.teams);newraw,newmeta,_=model._raw(newrows,'player',estimator.teams)
    if not np.array_equal(oldmeta,newmeta) or not np.array_equal(np.delete(oldraw,5,axis=1),np.delete(newraw,5,axis=1),equal_nan=True):raise ValueError('nonlineup model matrix changed')
    olds=model.predict(estimator,oldrows);news=model.predict(estimator,newrows);output=[];maxerr=0.
    for oldp,newp in zip(olds,news):
        fid=oldp['fixture_id'];reference=original[fid]
        maxerr=max(maxerr,abs(oldp['home_rate']-reference['home_rate']),abs(oldp['away_rate']-reference['away_rate']),*(abs(oldp['probabilities_3way'][k]-reference['probabilities_3way'][k]) for k in ('home','draw','away')))
        output.append({'fixture_id':fid,'event_date':base[fid]['event_date'],'original_player':reference,'counterfactual_player':newp,'frozen_team':team[fid],'original_lineup_values':{k:base[fid]['features'].get(k,{}).get('value') for k in LINEUP},'official_lineup_values':{k:newrows[len(output)]['features'].get(k,{}).get('value') for k in LINEUP}})
    if maxerr>1e-10:raise ValueError('frozen baseline model does not reproduce predictions')
    return {'scenario':SCENARIO,'source_scenario':DATA_SCENARIO,'official_lineup_is_confirmed_at_historic_time':False,'historical_receipt_proven':False,'eligible_as_pregame_feature':False,'point_in_time_verified':False,'pitcher':'prior projection, not official confirmed starter','original_2026_denominator':726,'official_lineup_count':len(output),'unprocessed_count':726-len(output),'total_players':len(output)*18,'missing_history_players':missing_players,'null_lineup_features':null_lineups,'nonlineup_history_sha256':nonlineup_hash.hexdigest(),'unchanged_non_lineup_model_matrix':True,'original_model_max_error':maxerr,'frozen_model_alpha':saved['alpha'],'inputs_sha256':HASH,'predictions':output}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--assume-official-batting-available',action='store_true');a=parser.parse_args()
    if not a.assume_official_batting_available:raise ValueError('explicit retrospective batting assumption required')
    result=build(load_inputs())
    with Path(a.output).open('x',encoding='utf8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:result[k] for k in ('official_lineup_count','total_players','missing_history_players','null_lineup_features','original_model_max_error','nonlineup_history_sha256')},ensure_ascii=False))
if __name__=='__main__':main()
