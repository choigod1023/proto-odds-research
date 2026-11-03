"""Repeat fixed refits and independently verify validation and final predictions."""
import gzip,json
from pathlib import Path
import dataset,independent_verify as independent,pipeline as p,runner
P=p.P
def main():
 pairs,labels,target,_=dataset.load();dev=json.loads((P/'artifacts/development.json').read_bytes());pred=json.loads((P/'artifacts/predictions.json').read_bytes());models=json.loads((P/'artifacts/final-models.json').read_bytes());result=json.loads((P/'artifacts/results.json').read_bytes());dev_target={s:[x for x in pairs[s] if x['season']==2025] for s in p.SELECTORS};dev_frozen={'models':dev['models'],'predictions':dev['predictions']};v=independent.verify_predictions(dev_frozen,dev_target);v.update(independent.verify_outcomes(dev_frozen,labels,{'results':{s:{'outcome':dev['results'][s]['metrics']} for s in p.SELECTORS}}));
 refit=runner.final_predictions(pairs,labels,target)
 if refit['models']!=models['models'] or refit['predictions']!=pred['predictions'] or refit['coverage']!=pred['coverage']:raise ValueError('fixed final refit failed exact replay')
 test_labels=p.index([json.loads(x) for x in gzip.decompress((P/'evaluation-inputs/test-labels.jsonl.gz').read_bytes()).splitlines()]);final=independent.verify_predictions({**pred,'models':models['models']},target);final.update(independent.verify_outcomes(pred,test_labels,result));final.update(independent.verify_settlements(result));final.update(independent.verify_paired_intervals(result));out={'validation':v,'final':final,'fixed_final_refit_exact':True,'no_new_hyperparameter_search':True};p.original.dump(P/'artifacts/verification.json',out);print(json.dumps(out))
if __name__=='__main__':main()
