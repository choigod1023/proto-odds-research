"""Two-stage execution: commit development before running final."""
import argparse, hashlib, json, subprocess
from pathlib import Path
import candidate
core=candidate.core
HERE=Path(__file__).resolve().parent

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def assert_committed(path):
 rel=path.relative_to(HERE.parents[2]).as_posix()
 if subprocess.run(['git','cat-file','-e','HEAD:'+rel],capture_output=True).returncode: raise ValueError('development artifact not committed: '+rel)
 if subprocess.run(['git','diff','--quiet','HEAD','--',rel]).returncode: raise ValueError('development artifact differs from commit: '+rel)

def run_development(data):
 tr,val=core.development(data); reference=core.baseline_reference()
 pred={'baseline':reference['development_predictions']}
 for key in candidate.CONFIGS:
  model=candidate.fit(tr,data['labels'],key)
  pp=candidate.predict(model,val)
  core.dump(HERE/'development'/key/'model.json',model)
  core.dump(HERE/'development'/key/'predictions.json',pp)
  pred[key]=pp
  print('development',key,flush=True)
 choice=core.choose(pred,data['labels'])
 choice['configurations']=candidate.CONFIGS
 choice['contract_sha256']=core.CONTRACT_SHA
 choice['baseline']='common/inputs/baseline-reference.json:development_predictions'
 core.dump(HERE/'development'/'selection.json',choice)
 print('selected',choice['selected'],flush=True)

def run_final(data):
 selection=HERE/'development'/'selection.json';assert_committed(selection)
 chosen=json.loads(selection.read_text(encoding='utf-8'))['selected']
 for config in candidate.CONFIGS:
  for file in ('model.json','predictions.json'):assert_committed(HERE/'development'/config/file)
 tr=core.final_training(data)
 if chosen=='baseline':
  model=core.fit_baseline(tr,data['labels']); actual=core.predict_baseline(model,data['test_actual']); projected=core.predict_baseline(model,data['test_projected'])
 else:
  model=candidate.fit(tr,data['labels'],chosen); actual=candidate.predict(model,data['test_actual']); projected=candidate.predict(model,data['test_projected'])
 core.dump(HERE/'final'/'model.json',model)
 core.dump(HERE/'final'/'actual.json',actual)
 core.dump(HERE/'final'/'projected.json',projected)
 info={'selected':chosen,'development_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'contract_sha256':core.CONTRACT_SHA,'train_count':len(tr),'actual_count':len(actual),'projected_count':len(projected),'test_outcomes_read':False,'sha256':{p:digest(HERE/'final'/p) for p in ('model.json','actual.json','projected.json')}}
 core.dump(HERE/'final'/'manifest.json',info)
 print(json.dumps(info,ensure_ascii=False),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('stage',choices=('development','final'));args=p.parse_args();data=core.load()
 (run_development if args.stage=='development' else run_final)(data)
