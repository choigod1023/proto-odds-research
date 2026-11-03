"""Refit matched four cells and old-full references, after development freeze."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import dataset,pipeline as p,runner

def main():
 args=argparse.ArgumentParser();args.add_argument('--development-commit',required=True);args.add_argument('--development-sha256',required=True);a=args.parse_args()
 if len(a.development_commit)!=40 or len(a.development_sha256)!=64:raise ValueError('full frozen development commit/SHA required')
 path=p.P/'artifacts/development.json';raw=path.read_bytes();blob=subprocess.check_output(['git','show',a.development_commit+':experiments/kbo-actual-training-20261103/artifacts/development.json'])
 if hashlib.sha256(raw).hexdigest()!=a.development_sha256 or raw!=blob:raise ValueError('development artifact must be committed before final refit')
 dev=json.loads(raw)
 if dev['2026_labels_read'] is not False or dev['family_or_hyperparameter_selection'] is not False:raise ValueError('unexpected development selection state')
 pairs,labels,target,manifest=dataset.load();out=runner.final_predictions(pairs,labels,target)
 if out['training_n']!=2182 or out['target_n']!=709:raise ValueError('unexpected fixed final cohort')
 meta={'development_commit':a.development_commit,'development_sha256':a.development_sha256,'paired_source_commit':dataset.EXPECTED_COMMIT,'paired_manifest_sha256':dataset.MAN_SHA,'contract_sha256':p.CONTRACT_SHA,'exclusions':manifest['excluded_fixtures'],'counts_by_training_season':out['counts_by_training_season'],'training_n':out['training_n'],'target_n':out['target_n'],'historical_receipt_verified':False,'actual_roster_is_retrospective':True,'2026_labels_read':False,'no_model_or_hyperparameter_selection':True,'context_full_training_differs_from_matched':True}
 p.original.dump(p.P/'artifacts/final-models.json',{**meta,'models':out['models']});p.original.dump(p.P/'artifacts/predictions.json',{**meta,'predictions':out['predictions'],'coverage':out['coverage']});print(json.dumps({'training_n':out['training_n'],'target_n':out['target_n'],'scenarios':list(out['predictions']),'cells':list(out['models'])},ensure_ascii=False))
if __name__=='__main__':main()
