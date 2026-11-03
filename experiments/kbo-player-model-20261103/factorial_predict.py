"""Freeze the four fixed-model factorial predictions; never reads outcomes."""
import argparse,copy,gzip,hashlib,importlib.util,json,sys
from datetime import date
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_factorial_preflight',P/'factorial_preflight.py');f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f);m=f.m
COMMIT='b2dbad4a21121fe1d79285bd898bfe2f43f3bad0'
HASH={'official_batting_only':'789a413ab35575b2461dd2d6943485e88be55f96e1eab9364b6e706691ee3e58','official_starter_only':'c5481b7fd7a36bc345e11ec953225ab659431408059cbb03fedab40050df6a46','official_batting_and_starter':'f16c08073737188b9bd2dec9de59791cdeb72033e6bba8c3061061809a2a8131','cohorts':'f8995e367882320398d2c7600ef08bc2cd9d709d35aeac33c4c8f1177f8ac7af'}
NAMES=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter')
H1SHA='5c0a9d88287e0bf84747003386cecac0a81684b169ec08604776de3669679c17'

def load():
    for name,(commit,sha) in f.CONTRACTS.items():f.exact_blob(m.ORCH,commit,Path('experiments/kbo-player-challenger-20261103')/name,sha)
    official_repo=Path('C:/Users/user/Documents/Codex/klu')
    f.exact_blob(official_repo,'b24739cf52435070586f8687011fd473a16123e4',Path('experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl'),'f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3')
    x=m.load_inputs();sets={}
    for name in NAMES[1:]:
        path=Path('experiments/kbo-player-data-20261103')/('official-full-2026-'+name+'.jsonl.gz')
        sets[name]=m.index([json.loads(z) for z in gzip.decompress(f.exact_blob(m.DATA,COMMIT,path,HASH[name])).splitlines()])
    path=Path('experiments/kbo-player-data-20261103/official-full-2026-cohorts.jsonl');cohorts=json.loads(f.exact_blob(m.DATA,COMMIT,path,HASH['cohorts']))
    raw=m.read_checked(P/'phase4/predictions.json',H1SHA);h1=json.loads(raw)
    blob=__import__('subprocess').check_output(['git','-C',str(m.ROOT),'show','516d5068:experiments/kbo-player-model-20261103/phase4/predictions.json'])
    if raw!=blob:raise ValueError('H1 prediction blob mismatch')
    return x,sets,cohorts,h1

def build(x,sets,cohorts,h1):
    base=m.index(x['features']);prior=m.index(x['prior']);h1pred={v['fixture_id']:v for v in h1['predictions']};ids=set(cohorts['all']);old=set(cohorts['original_193']);new=set(cohorts['new_complement']);excluded={x['fixture_id'] for x in cohorts['excluded']}
    frozen={v['fixture_id']:v for v in x['predictions']['predictions']['player']};team={v['fixture_id']:v for v in x['predictions']['predictions']['team']}
    if (len(ids),len(old),len(new),len(excluded))!=(709,193,516,17) or old!=set(h1pred) or old|new!=ids or old&new or ids&excluded or ids|excluded!=set(frozen) or any(set(v)!=ids for v in sets.values()):raise ValueError('cohort mismatch')
    f.validate.dates={fid:date.fromisoformat(v['event_date']) for fid,v in base.items()};rows={k:[] for k in NAMES};ordered=sorted(ids,key=lambda fid:(base[fid]['event_date'],fid));missing=0
    for fid in ordered:
        priorrow=prior[fid];lineup=sets['official_batting_only'][fid];starter=sets['official_starter_only'][fid];both=sets['official_batting_and_starter'][fid]
        missing+=sum(v is None for v in f.validate(priorrow,lineup,starter,both,base[fid]))
        rows['original_projected'].append(base[fid])
        for name,src in [('official_batting_only',lineup),('official_starter_only',starter),('official_batting_and_starter',both)]:
            row=copy.deepcopy(base[fid]);changed=(f.BAT if name=='official_batting_only' else f.STARTER if name=='official_starter_only' else f.BAT+f.STARTER)
            for key in changed:
                entry=src['features'][key];row['features'][key]={'value':m.adapter.scalar(key,entry['value']),'available_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received','source_id':'batters' if key in f.BAT else 'pitchers','history_fixture_ids':entry['source_fixture_ids'],'history_latest_date':entry['latest_source_date'],'history_max_completed_at':None}
            if any(row['features'].get(k)!=base[fid]['features'].get(k) for k in base[fid]['features'] if k not in changed):raise ValueError('unchanged model feature modified')
            rows[name].append(row)
    if missing!=59:raise ValueError('unexpected missing starter scalar count')
    saved=x['predictions']['models']['player'];m.model.fit=m.forbid_training;m.model.minimize=m.forbid_training
    estimator=m.model.ScoreModel('player',tuple(saved['teams']),np.array(saved['mean']),np.array(saved['scale']),np.array(saved['beta']),saved['alpha'],True)
    if saved['alpha']!=100.:raise ValueError('alpha mismatch')
    predictions={k:m.model.predict(estimator,rows[k]) for k in NAMES};out=[];max_base=0.;max_h1=0.
    for i,fid in enumerate(ordered):
        basep=predictions['original_projected'][i];reference=frozen[fid]
        for key in ('home_rate','away_rate'):max_base=max(max_base,abs(basep[key]-reference[key]))
        for key in ('home','draw','away'):max_base=max(max_base,abs(basep['probabilities_3way'][key]-reference['probabilities_3way'][key]))
        if fid in old:
            a=predictions['official_batting_only'][i];b=h1pred[fid]['counterfactual_player']
            for key in ('home_rate','away_rate'):max_h1=max(max_h1,abs(a[key]-b[key]))
            for key in ('home','draw','away'):max_h1=max(max_h1,abs(a['probabilities_3way'][key]-b['probabilities_3way'][key]))
        out.append({'fixture_id':fid,'event_date':base[fid]['event_date'],'cohort':'original_193' if fid in old else 'new_complement','frozen_team':team[fid],'settings':{k:predictions[k][i] for k in NAMES}})
    if max(max_base,max_h1)>1e-10:raise ValueError('frozen baseline/H1 predictions do not reproduce')
    return {'scenario':'retrospective_fixed_model_factorial_not_pit','source_commit':COMMIT,'input_hashes':HASH,'h1_predictions_sha256':H1SHA,'counts':{'all':709,'original_193':193,'new_complement':516,'excluded_identity':17,'starter_scalar_null_sides':missing},'nonlineup_nonstarter_unchanged':True,'historical_receipt_verified':False,'eligible_pregame':False,'original_model_max_error':max_base,'original_193_h1_max_error':max_h1,'predictions':out}

def main():
    a=argparse.ArgumentParser();a.add_argument('--assume-retrospective-availability',action='store_true');a.add_argument('--output',required=True);args=a.parse_args()
    if not args.assume_retrospective_availability:raise ValueError('explicit retrospective assumption required')
    out=build(*load());p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf8',newline='\n') as fp:json.dump(out,fp,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);fp.write('\n')
    print(json.dumps({k:out[k] for k in ('counts','original_model_max_error','original_193_h1_max_error')}))
if __name__=='__main__':main()

