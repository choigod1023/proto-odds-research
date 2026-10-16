"""Recover completed atomic archives and continue the unchanged 9,999 budget."""
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import multiprocessing
import os
from pathlib import Path
import time
from lotto645 import data_hash,load_draws_jsonl
from lotto_bias_null import summarize_seeds
from lotto_null_extension import text_hash,tail_precision
from lotto_null_9999 import read_archive,slim,initialize,run_history,digest


def durable_json(path,value):
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(value,f,indent=2); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def durable_archive(path,rows):
    tmp=path.with_suffix('.tmp')
    with tmp.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as zipped:
            for row in rows: zipped.write((json.dumps(row,separators=(',',':'))+'\n').encode())
        raw.flush(); os.fsync(raw.fileno())
    tmp.replace(path)
    if read_archive(path)!=rows: raise ValueError('archive read-back differs')


def main():
    root=Path(__file__).resolve().parents[1]; out=root/'data/lotto/reports/null-9999'
    protocol=json.loads((out/'protocol.json').read_text())
    for name in ('src/lotto_bias_null.py','src/lotto_seed_128.py','src/lotto_seed_expanded.py','src/lotto_seed_scan.cpp'):
        if text_hash(root/name)!=protocol['frozen_source_lf_sha256'][name]: raise ValueError('frozen source changed')
    if text_hash(root/'src/lotto_seed_scan_fast.cpp')!=protocol['optimized_source_lf_sha256']: raise ValueError('optimized source changed')
    if data_hash(load_draws_jsonl(root/'data/lotto/draws.jsonl'))!=protocol['ledger_hash']: raise ValueError('ledger changed')
    exe=root/'.local-build/scan-fast.exe'
    if digest(exe)!=protocol['executable_sha256']: raise ValueError('executable changed')
    quarantine=root/'.local-build/interrupted-files'; quarantine.mkdir(exist_ok=True)
    for name in ('archive-manifest.json','verification-progress.json'):
        path=out/name
        if path.exists():
            try: json.loads(path.read_text())
            except (ValueError,UnicodeError): path.rename(quarantine/name)
    manifests=[]; summaries=[]; missing=[]; damaged=[]
    for first,last in [(0,999)]+[(i,i+99) for i in range(1000,10000,100)]:
        path=out/f'histories-{first:05d}-{last:05d}.jsonl.gz'
        if path.exists():
            try:
                rows=read_archive(path)
                if [r['history_index'] for r in rows]!=list(range(first,last+1)): raise ValueError('coverage')
                if first==0 and digest(path)!=protocol['prefix_archive_sha256']: raise ValueError('original prefix differs')
            except (OSError,EOFError,ValueError,UnicodeError) as e:
                if first==0: raise
                damaged.append({'file':path.name,'reason':str(e),'sha256':digest(path)})
                path.rename(quarantine/path.name); missing.append((first,last)); continue
            manifests.append({'file':path.name,'first':first,'last':last,'sha256':digest(path)})
            summaries.extend(slim(row) for row in rows)
        else: missing.append((first,last))
    durable_json(out/'recovery.json',{'verified_existing_histories':len(summaries),'recomputed_blocks':damaged,
        'missing_blocks':missing,'resume_source_lf_sha256':text_hash(__file__),
        'cause':'Original tool sessions/processes disappeared; two trailing archives contained zero bytes. Exact interruption cause unknown.',
        'selection_unchanged':True,'durability':'fsync before rename plus archive read-back verification'})
    durable_json(out/'archive-manifest.json',manifests)
    print(f'Recovered {len(summaries)} histories; {len(missing)} blocks to compute',flush=True)
    start=time.perf_counter()
    with ProcessPoolExecutor(max_workers=protocol['workers'],mp_context=multiprocessing.get_context('spawn'),
        initializer=initialize,initargs=(str(root/'data/lotto/draws.jsonl'),str(exe))) as pool:
        for first,last in missing:
            rows=list(pool.map(run_history,range(first,last+1),chunksize=1))
            path=out/f'histories-{first:05d}-{last:05d}.jsonl.gz'; durable_archive(path,rows)
            manifests.append({'file':path.name,'first':first,'last':last,'sha256':digest(path)})
            manifests.sort(key=lambda m:m['first']); durable_json(out/'archive-manifest.json',manifests)
            summaries.extend(slim(row) for row in rows)
            print(f'completed through history {last}/9999; resume elapsed={time.perf_counter()-start:.1f}s',flush=True)
    summaries.sort(key=lambda r:r['history_index'])
    if [r['history_index'] for r in summaries]!=list(range(10000)): raise ValueError('final coverage mismatch')
    summary=summarize_seeds(summaries)
    for label in ('validation','final'):
        summary[label]['monte_carlo_precision']=tail_precision(summaries[0][label]['z_against_matched_controls'],
            [r[label]['z_against_matched_controls'] for r in summaries[1:] if r[label]['status']=='evaluated'])
    durable_json(out/'seed-summary.json',summary)
    durable_json(out/'timing.json',{'extension_seconds':None,'resume_seconds':time.perf_counter()-start,
        'note':'Original elapsed-time record was interrupted; do not report a fabricated total runtime.'})
    print('COMPLETE',flush=True)


if __name__=='__main__': main()
