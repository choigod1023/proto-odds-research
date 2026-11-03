"""Only score exact committed 2026 predictions, on pinned original prices."""
import argparse,gzip,hashlib,json
from pathlib import Path
import dataset,independent_verify,pipeline as p,settlement,source_adapter,workflow
P=p.P;E=P/'evaluation-inputs';OLD=Path('C:/Users/user/Documents/Codex/kbeffects');OLD_COMMIT='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';SOURCE='experiments/kbo-player-effects-20261103/inputs/'
FILES={'test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','price-ledgers.json':'24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23','price-reject-audit.jsonl.gz':'629c26c049669b07c6f0fc7413cd9c55a86beba24b20ce75767f2f54e82a2d93','source-labels.jsonl.gz':'1abe7313d5f71cfc3e7a7600665fd2d5f97e4230f1ce6afe7a597f561cfd6e2b'}
def input_loader():
 E.mkdir(exist_ok=True);hashes={}
 for name,sha in FILES.items():
  raw=source_adapter.committed_blob(OLD,OLD_COMMIT,SOURCE+name,sha);path=E/name
  if path.exists() and path.read_bytes()!=raw:raise ValueError('existing evaluation asset differs')
  if not path.exists():path.write_bytes(raw)
  hashes[name]=sha
 if hashlib.sha256(gzip.decompress((E/'source-labels.jsonl.gz').read_bytes())).hexdigest()!='ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca':raise ValueError('original label bytes differ')
 def gz(name):return p.index([json.loads(z) for z in gzip.decompress((E/name).read_bytes()).splitlines()])
 labels=gz('test-labels.jsonl.gz');source=gz('source-labels.jsonl.gz');audit=gz('price-reject-audit.jsonl.gz');prices=json.loads((E/'price-ledgers.json').read_bytes())
 if len(labels)!=726 or not set(labels)<=set(source) or any(any(v[k]!=source[fid][k] for k in ('home_score','away_score')) for fid,v in labels.items()):raise ValueError('source test label mismatch')
 manifest={'source_commit':OLD_COMMIT,'source_git_paths':SOURCE,'files':hashes,'outcomes_loaded_after_prediction_blob_verification':True};(E/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
 return labels,prices['ledgers'],audit

def training_coverage(pairs,models):
 result={}
 for name,model in models.items():
  selector='actual' if name in ('M0_actual','M2_actual') else 'projected';result[name]={'selector':selector,'old_full_context_uses_matched_subset_only':name.startswith('oldfull_'),'years':{}}
  for year in (2023,2024,2025):
   rows=[x for x in pairs[selector] if x['season']==year];flags=[p.original.side_features(row,side,model['spec']) for row in rows for side in ('home','away')];result[name]['years'][str(year)]={'team_games':len(flags),'missing_lineups':int(sum(z[0] for z in flags)),'unknown_hitter_slots':int(round(9*sum(z[1] for z in flags))),'missing_starters':int(sum(z[2] for z in flags)),'unknown_starters':int(sum(z[3] for z in flags))}
 return result

def main():
 arg=argparse.ArgumentParser();arg.add_argument('--prediction-commit',required=True);arg.add_argument('--prediction-sha256',required=True);a=arg.parse_args();path=P/'artifacts/predictions.json';repo=P.parents[1]
 # The callback cannot materialize or read outcome-bearing inputs until freeze verification succeeds.
 result=workflow.settle_after_freeze(path,a.prediction_commit,a.prediction_sha256,repo,input_loader)
 pairs,dev_labels,target,manifest=dataset.load();frozen=workflow.frozen_predictions(path,a.prediction_commit,a.prediction_sha256,repo);models_path=P/'artifacts/final-models.json';model_raw=models_path.read_bytes();import subprocess
 blob=subprocess.check_output(['git','show',a.prediction_commit+':experiments/kbo-actual-training-20261103/artifacts/final-models.json'])
 if blob!=model_raw:raise ValueError('models not frozen with predictions')
 models=json.loads(model_raw);independent=independent_verify.verify_predictions({**frozen,'models':models['models']},target);independent.update(independent_verify.verify_settlements(result));labels,_,_=input_loader();independent.update(independent_verify.verify_outcomes(frozen,labels,result));independent.update(independent_verify.verify_paired_intervals(result))
 dev=json.loads((P/'artifacts/development.json').read_bytes());pairs_to_compare=(('M0_actual','M0_projected'),('M2_actual','M2_projected'),('M2_projected','M0_projected'),('M2_actual','M0_actual'));dev_pairs={selector:{a+'_vs_'+b:settlement.outcome_paired(preds,dev_labels,a,b) for a,b in pairs_to_compare} for selector,preds in dev['predictions'].items()}
 result.update({'prediction_commit':a.prediction_commit,'prediction_sha256':a.prediction_sha256,'final_model_sha256':hashlib.sha256(model_raw).hexdigest(),'source_commit':dataset.EXPECTED_COMMIT,'source_manifest_sha256':dataset.MAN_SHA,'training_count':2182,'target_count':709,'excluded_fixtures':manifest['excluded_fixtures'],'coverage':frozen['coverage'],'training_coverage_by_season':training_coverage(pairs,models['models']),'development_paired':dev_pairs,'independent_verification':independent,'retrospective_not_blind':True,'actual_wallet_replay':False,'source_2024_request_spacing_violations_under_1_5_seconds':manifest['source_rosters']['2024']['request_spacing_violations_under_1_5_seconds']});p.original.dump(P/'artifacts/results.json',result)
 print(json.dumps({'independent':independent,'scenarios':{s:{'threeway':{k:v['threeway'] for k,v in z['outcome'].items()},'price_n':{k:x['n'] for k,x in z['price_cohorts'].items()}} for s,z in result['results'].items()}},ensure_ascii=False))
if __name__=='__main__':main()
