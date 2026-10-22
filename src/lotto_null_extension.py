"""Increase synthetic histories without changing the frozen selection rule."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.stats import beta
from lotto645 import data_hash,load_draws_jsonl
from lotto_bias_null import seed_history,summarize_seeds
from lotto_seed_128 import save


def text_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def tail_precision(actual_z,fake_z):
    count=sum(v>=actual_z for v in fake_z); n=len(fake_z)
    return {'exceedances':count,'histories':n,'p_mc':(count+1)/(n+1),
        'underlying_tail_probability_interval_95':[float(beta.ppf(.025,count,n-count+1)) if count else 0.,
            float(beta.ppf(.975,count+1,n-count)) if count<n else 1.],
        'interpretation':'binomial Monte Carlo uncertainty conditional on the frozen procedure, not effect-size confidence'}


def main():
    root=Path(__file__).resolve().parents[1]; p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--output',type=Path,default=root/'data/lotto/reports/null-expanded')
    p.add_argument('--total-fake',type=int,default=999)
    a=p.parse_args()
    old=json.loads((a.prior/'protocol.json').read_text()); env=json.loads((a.prior/'environment.json').read_text())
    for name in ('src/lotto_bias_null.py','src/lotto_seed_128.py','src/lotto_seed_expanded.py','src/lotto_seed_scan.cpp'):
        if text_hash(root/name)!=env['source_lf_sha256'][name]: raise ValueError(f'frozen source mismatch: {name}')
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    if data_hash(draws)!=old['ledger_hash'] or len(draws)!=1238 or old['candidates']!=1000000 or old['controls']!=199:
        raise ValueError('frozen data/budget mismatch')
    if a.total_fake<=old['fake_histories'] or a.output.exists(): raise ValueError('use larger budget and new output')
    a.output.mkdir(parents=True)
    protocol={'prior_protocol':old,'total_fake_histories':a.total_fake,'reused_fake_histories':old['fake_histories'],
        'new_fake_histories':a.total_fake-old['fake_histories'],'workers':4,
        'frozen_source_lf_sha256':{name:text_hash(root/name) for name in env['source_lf_sha256'] if (root/name).exists()},
        'extension_source_lf_sha256':text_hash(__file__),'executable_sha256':hashlib.sha256(a.executable.read_bytes()).hexdigest(),
        'primary_endpoints':'original validation and final standardized mean matches; same Bonferroni correction for two splits',
        'new_rounds_plan':{'first':1239,'last_cap':1245,'selection':'unchanged original actual survivors',
            'controls':9999,'odd_control_seed':12820261015,'even_control_seed':12820261016,
            'metric':'mean matches, one upper-tail matched-sharing test across all available new rounds',
            'caveat':'previously unused by this experiment, already published; not prospective, only up to seven draws'},
        'no_optional_stopping':True,'ledger_hash':data_hash(draws)}
    save(a.output/'protocol.json',protocol)
    rows=[json.loads((a.prior/f'history-{i:03d}.json').read_text()) for i in range(old['fake_histories']+1)]
    save(a.output/'prefix-manifest.json',{'prior_commit':'389a30a2','history_canonical_sha256':{str(i):canonical_hash(r) for i,r in enumerate(rows)}})
    start=time.perf_counter()
    actual=seed_history(0,draws,a.executable.resolve(),old['candidates'],old['controls'])
    if actual!=rows[0]: raise ValueError('actual rerun differs from frozen result')
    save(a.output/'actual-frozen.json',actual)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(seed_history,i,draws,a.executable.resolve(),old['candidates'],old['controls'])
                 for i in range(old['fake_histories']+1,a.total_fake+1)]
        for f in futures: rows.append(f.result())
    summary=summarize_seeds(rows)
    for label in ('validation','final'):
        summary[label]['monte_carlo_precision']=tail_precision(rows[0][label]['z_against_matched_controls'],
            [r[label]['z_against_matched_controls'] for r in rows[1:] if r[label]['status']=='evaluated'])
    with (a.output/'histories.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as zipped:
            for row in rows: zipped.write((json.dumps(row,separators=(',',':'))+'\n').encode())
    save(a.output/'seed-summary.json',summary)
    save(a.output/'timing.json',{'seconds':time.perf_counter()-start,'actual_rerun_identical':True})
    print(json.dumps({k:summary[k]['monte_carlo_precision'] for k in ('validation','final')}),flush=True)


if __name__=='__main__': main()
