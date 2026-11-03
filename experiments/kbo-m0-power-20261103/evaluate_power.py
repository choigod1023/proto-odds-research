"""Repeat the frozen power-feature evaluation without training or reselection."""
import argparse,gzip,hashlib,json,sys
from pathlib import Path

P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kbo-m0-win-head-20261103'))
import evaluate as common

def artifact(year):
    folder=P/f'artifacts/{year}';manifest=json.loads((folder/'manifest.json').read_bytes())
    for name,key in (('model.json','model_sha256'),('predictions.json.gz','predictions_sha256')):
        if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=manifest[key]:raise ValueError('power artifact changed')
    if hashlib.sha256((P/'contract.json').read_bytes()).hexdigest()!=manifest['contract_sha256']:raise ValueError('power contract changed')
    return json.loads(gzip.decompress((folder/'predictions.json.gz').read_bytes()))

def main(verify_existing=False):
    common.train.core.verify()
    forecasts={y:artifact(y) for y in (2024,2025,2026)}
    labels,prices,strict,_=common.evaluation_inputs(forecasts)
    result={'all_periods_previously_seen':True,'annual':{},'candidate':'power_m0',
            'development_gate_passed':False,'freeze_commit':'f405c56bdf0f341c63c36bbb838c491ea4ce28c6',
            'no_collector_or_automations_changes':True,'total_new_candidates_in_task':2}
    for year in (2024,2025,2026):
        result['annual'][str(year)]=common.score(forecasts[year],labels[year],prices[year],'power_m0')
        reference=common.artifact(year)['baseline']
        if reference!=forecasts[year]['baseline']:raise ValueError('baseline forecast changed')
    d=result['annual']['2024']['models']
    result['development_gate_passed']=d['power_m0']['outcome']['decisive_hits']>d['baseline']['outcome']['decisive_hits'] and d['power_m0']['outcome']['decisive_logloss']<=d['baseline']['outcome']['decisive_logloss']
    result['strict2026']=common.score(forecasts[2026],labels[2026],strict,'power_m0')
    if verify_existing:
        if result!=json.loads((P/'results.json').read_bytes()):raise ValueError('saved power result does not reproduce')
        print('saved power result reproduced exactly')
    else:common.train.write(P/'results.json',result)
    for y,r in result['annual'].items():
        m=r['models']['power_m0'];print(y,'decisive hits',m['outcome']['decisive_hits'],'/',m['outcome']['decisive'],'ROI',m['price']['roi'])

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--verify-existing',action='store_true');main(ap.parse_args().verify_existing)
