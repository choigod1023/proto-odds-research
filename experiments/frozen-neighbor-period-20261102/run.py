"""Evaluate only the registered later-period inputs; no tuning or live writes."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('period_frozen_engine',HERE.parent/'frozen-neighbor-engine-20261102/engine.py')
engine=importlib.util.module_from_spec(spec);sys.modules[spec.name]=engine;spec.loader.exec_module(engine)


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def frozen_paths():
    return [ROOT/'docs/research/2026-11-02-frozen-neighbor-period-protocol.md',
      ROOT/'src/devig.py',HERE.parent/'frozen-neighbor-engine-20261102/engine.py',
      *[HERE/x for x in ('run.py','prepare.py','capture_official.py','vendor/frozen_dc.py',
       'inputs/frozen-bank-source.jsonl','inputs/official-history-2023-2025.jsonl',
       'inputs/domestic-2026.raw.jsonl','inputs/source-manifest.json','official-2026/fixtures.jsonl',
       'official-2026/manifest.json','official-2026/requests.json','verified-inputs/dc-states.json',
       'verified-inputs/domestic-opportunities.json','verified-inputs/overseas-opportunities.json',
       'verified-inputs/preparation.json','INITIAL_RUN_STATUS.md')],
      ROOT/'data/raw/overseas/K리그1.json',*[HERE/'official-2026'/f'{m:02d}.json' for m in range(1,12)]]


def verify_registration(commit):
    subprocess.run(['git','merge-base','--is-ancestor',commit,'HEAD'],cwd=ROOT,check=True)
    for p in frozen_paths():
        blob=subprocess.check_output(['git','show',commit+':'+p.relative_to(ROOT).as_posix()],cwd=ROOT)
        if blob.replace(b'\r\n',b'\n')!=p.read_bytes().replace(b'\r\n',b'\n'):
            raise ValueError('code/input changed since registration: '+str(p))


def build(commit):
    verify_registration(commit)
    bank=engine.load_bank(HERE/'inputs/frozen-bank-source.jsonl')
    report={'classification':'fixed_candidates_new_NN_period_but_used_in_other_research_not_blind',
      'registration_commit':commit,'cohorts':{},'automatic_adoption':False,
      'eligibility_correction':'Initial overseas market/order assertion was rejected; all120 excluded, models unchanged',
      'actual_operating_or_tipster_or_9to1_comparison':False,
      'monthly_wallet_loss_cap_changed':False,
      'input_code_lf_sha256':{p.relative_to(ROOT).as_posix():digest(p) for p in frozen_paths()}}
    ledgers={};identity={}
    for cohort in ('domestic','overseas'):
        rows=json.loads((HERE/'verified-inputs'/(cohort+'-opportunities.json')).read_text(encoding='utf8'))
        result,ledger=engine.evaluate(rows,bank)
        report['cohorts'][cohort]=result;ledgers[cohort]=ledger
        identity[cohort]={r['id'] for r in ledger}
    report['distinct_fixtures']=len(identity['domestic']|identity['overseas'])
    report['overlapping_fixtures']=len(identity['domestic']&identity['overseas'])
    report['no_combined_ROI']='Do not pool overlapping fixtures or different price sources'
    return report,ledgers


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--registration',required=True);args=p.parse_args()
    destination=HERE/'verified-results'
    output=destination/'results.json'
    if destination.exists():
        raise ValueError('results already exist; independently verify rather than silently overwrite')
    report,ledgers=build(args.registration)
    destination.mkdir(exist_ok=False)
    with output.open('x',encoding='utf8',newline='\n') as f:
        json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    for name,rows in ledgers.items():
        with (destination/(name+'-ledger.jsonl')).open('x',encoding='utf8',newline='\n') as f:
            for r in rows:f.write(json.dumps(r,sort_keys=True)+'\n')
    print(json.dumps({name:r['models'] for name,r in report['cohorts'].items()},ensure_ascii=True))
