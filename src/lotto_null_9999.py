"""Fixed-budget 9,999-history continuation with exact faster arithmetic."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import contextlib
import gzip
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import shutil
import time
from lotto645 import data_hash,load_draws_jsonl
from lotto_bias_null import seed_history,summarize_seeds
from lotto_null_extension import text_hash,canonical_hash,tail_precision
from lotto_seed_128 import save


def read_archive(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def write_archive(path,rows):
    temporary=path.with_suffix('.tmp')
    with temporary.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as zipped:
            for r in rows: zipped.write((json.dumps(r,separators=(',',':'))+'\n').encode())
    temporary.replace(path)


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slim(row):
    if row['history_index']==0: return row
    return {**{k:row[k] for k in ('history_index','pair_count','maximum_training_hits')},
        **{key:{k:row[key][k] for k in ('status','mean','z_against_matched_controls')} if row[key]['status']=='evaluated'
           else row[key] for key in ('validation','final')}}


def initialize(data,executable):
    global DRAWS,EXE
    DRAWS=load_draws_jsonl(data); EXE=Path(executable)


def run_history(index):
    with contextlib.redirect_stdout(io.StringIO()):
        return seed_history(index,DRAWS,EXE,1000000,199)


def main():
    root=Path(__file__).resolve().parents[1]; p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--reference-executable',type=Path,required=True)
    p.add_argument('--output',type=Path,default=root/'data/lotto/reports/null-9999')
    p.add_argument('--workers',type=int,default=8)
    a=p.parse_args()
    old=json.loads((a.prior/'protocol.json').read_text())
    for name in ('src/lotto_bias_null.py','src/lotto_seed_128.py','src/lotto_seed_expanded.py','src/lotto_seed_scan.cpp'):
        if text_hash(root/name)!=old['frozen_source_lf_sha256'][name]: raise ValueError('frozen source changed')
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    if data_hash(draws)!=old['ledger_hash'] or old['total_fake_histories']!=999 or not 1<=a.workers<=8:
        raise ValueError('incompatible input or worker budget')
    if a.output.exists(): raise ValueError('use a new output directory')
    previous=read_archive(a.prior/'histories.jsonl.gz')
    if [r['history_index'] for r in previous]!=list(range(1000)): raise ValueError('prefix coverage mismatch')
    # Before extending, compare complete outputs, not just survivor counts.
    checks=[]
    for index in (0,1,623,999):
        with contextlib.redirect_stdout(io.StringIO()):
            fast=seed_history(index,draws,a.executable.resolve(),1000000,199)
            reference=seed_history(index,draws,a.reference_executable.resolve(),1000000,199)
        if fast!=reference or fast!=previous[index]: raise ValueError('optimized implementation changed result')
        checks.append({'history_index':index,'canonical_sha256':canonical_hash(fast),'identical':True})
    a.output.mkdir(parents=True)
    protocol={'total_fake_histories':9999,'reused_fake_histories':999,'new_fake_histories':9000,
        'candidates_per_half':1000000,'controls_per_history':199,'training':[1,740],
        'validation':[741,990],'final':[991,1238],'history_seed':'645000+index',
        'generator':'splitmix64-paired128-v1','selection':'same 2+2 threshold, two constructed anchors',
        'workers':a.workers,'fixed_stopping_budget':9999,'ledger_hash':data_hash(draws),
        'original_protocol':old['prior_protocol'],'prefix_commit':'e3ab171b',
        'prefix_archive_sha256':digest(a.prior/'histories.jsonl.gz'),
        'frozen_source_lf_sha256':old['frozen_source_lf_sha256'],
        'optimized_source_lf_sha256':text_hash(root/'src/lotto_seed_scan_fast.cpp'),
        'runner_source_lf_sha256':text_hash(__file__),'executable_sha256':digest(a.executable),
        'reference_executable_sha256':digest(a.reference_executable),
        'arithmetic_equivalence_checks':checks,
        'primary_comparison':'same standardized mean match statistic and Bonferroni across original two splits',
        'new_rounds':'no new outcomes added; prior 1239-1245 evaluation retained unchanged',
        'caveat':'increased Monte Carlo precision on already inspected historical outcomes, not new predictive evidence'}
    save(a.output/'protocol.json',protocol)
    shutil.copyfile(a.prior/'histories.jsonl.gz',a.output/'histories-00000-00999.jsonl.gz')
    save(a.output/'retained-new-rounds.json',json.loads((a.prior/'new-rounds/evaluation.json').read_text()))
    summary_rows=[slim(r) for r in previous]; del previous
    manifests=[{'file':'histories-00000-00999.jsonl.gz','first':0,'last':999,'sha256':protocol['prefix_archive_sha256']}]
    start=time.perf_counter()
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('spawn'),
        initializer=initialize,initargs=(str(root/'data/lotto/draws.jsonl'),str(a.executable.resolve()))) as pool:
        for first in range(1000,10000,100):
            rows=list(pool.map(run_history,range(first,first+100),chunksize=1))
            if [r['history_index'] for r in rows]!=list(range(first,first+100)): raise ValueError('coverage mismatch')
            name=f'histories-{first:05d}-{first+99:05d}.jsonl.gz'
            write_archive(a.output/name,rows)
            manifests.append({'file':name,'first':first,'last':first+99,'sha256':digest(a.output/name)})
            save(a.output/'archive-manifest.json',manifests)
            summary_rows.extend(slim(r) for r in rows)
            print(f'completed fake histories={first+99}/9999; elapsed={time.perf_counter()-start:.1f}s',flush=True)
    summary=summarize_seeds(summary_rows)
    for label in ('validation','final'):
        summary[label]['monte_carlo_precision']=tail_precision(summary_rows[0][label]['z_against_matched_controls'],
            [r[label]['z_against_matched_controls'] for r in summary_rows[1:] if r[label]['status']=='evaluated'])
    save(a.output/'seed-summary.json',summary)
    save(a.output/'timing.json',{'extension_seconds':time.perf_counter()-start})
    print(json.dumps({label:summary[label]['monte_carlo_precision'] for label in ('validation','final')}))


if __name__=='__main__': main()
