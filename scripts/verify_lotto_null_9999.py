"""Follow atomic checkpoints and independently verify every surviving seed."""
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import data_hash,load_draws_jsonl
from lotto_bias_null import simulate,summarize_seeds
from lotto_null_9999 import read_archive,slim
from lotto_null_extension import tail_precision,text_hash
from lotto_null_resume import durable_json as save

MASK=(1<<64)-1; N=math.comb(45,6); STEP=0x9E3779B97F4A7C15


def scalar_rank(seed,draw):
    x=(seed+draw*STEP)&MASK
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&MASK
    x=((x^(x>>27))*0x94D049BB133111EB)&MASK
    return (x^(x>>31))%N


def wait_for(path):
    # The producer writes archives atomically. A bounded wait prevents an
    # abandoned producer from making this standalone verifier wait forever.
    deadline=time.monotonic()+7200
    while not path.exists():
        if time.monotonic()>deadline: raise TimeoutError(str(path))
        time.sleep(2)


def main():
    root=Path(__file__).resolve().parents[1]; out=root/'data/lotto/reports/null-9999'
    protocol=json.loads((out/'protocol.json').read_text())
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    assert data_hash(draws)==protocol['ledger_hash']
    for name,expected in protocol['frozen_source_lf_sha256'].items():
        if (root/name).exists(): assert text_hash(root/name)==expected
    assert text_hash(root/'src/lotto_seed_scan_fast.cpp')==protocol['optimized_source_lf_sha256']
    actual=np.array([d.numbers_sorted for d in draws])-1
    blocks=[(0,999)]+[(first,first+99) for first in range(1000,10000,100)]
    summaries=[]; half_seeds=0; checks=0; manifest=[]
    for first,last in blocks:
        name=f'histories-{first:05d}-{last:05d}.jsonl.gz'; path=out/name; wait_for(path)
        records=read_archive(path)
        assert [r['history_index'] for r in records]==list(range(first,last+1))
        checksum=hashlib.sha256(path.read_bytes()).hexdigest()
        if first==0: assert checksum==protocol['prefix_archive_sha256']
        manifest.append({'file':name,'first':first,'last':last,'sha256':checksum})
        for row in records:
            index=row['history_index']
            numbers=actual if index==0 else simulate(np.random.default_rng(645000+index),len(draws))
            targets=[sum(math.comb(int(n),k) for k,n in enumerate(ns,1)) for ns in numbers[:740]]
            for key,parity in [('odd',1),('even',0)]:
                half=row[key]; histogram=half['histogram']; observed=[0]*len(histogram)
                assert sum(histogram)==1000000 and histogram[0]==0
                assert len({r['seed'] for r in half['survivors']})==len(half['survivors'])
                pairs=[(i+1,t) for i,t in enumerate(targets) if (i+1)%2==parity]
                for survivor in half['survivors']:
                    seed=int(survivor['seed'])
                    hits=sum(scalar_rank(seed,d)==target for d,target in pairs)
                    assert hits==survivor['hits'] and hits>=2
                    observed[hits]+=1; half_seeds+=1; checks+=len(pairs)
                assert histogram[2:]==observed[2:]
            assert row['pair_count']==len(row['odd']['survivors'])*len(row['even']['survivors'])
            assert row['maximum_training_hits']==sum(max(i for i,v in enumerate(row[k]['histogram']) if v) for k in ('odd','even'))
            summaries.append(slim(row))
        save(out/'verification-progress.json',{'last_history':last,'half_seeds':half_seeds,'scalar_comparisons':checks})
        if last%1000==999: print(f'independent verification through history {last}',flush=True)
    wait_for(out/'timing.json')
    assert manifest==json.loads((out/'archive-manifest.json').read_text())
    summary=summarize_seeds(summaries)
    for label in ('validation','final'):
        summary[label]['monte_carlo_precision']=tail_precision(summaries[0][label]['z_against_matched_controls'],
            [r[label]['z_against_matched_controls'] for r in summaries[1:] if r[label]['status']=='evaluated'])
    assert summary==json.loads((out/'seed-summary.json').read_text())
    result={'status':'passed','histories':len(summaries),'unchanged_prefix_histories':1000,
        'scalar_half_seeds_checked':half_seeds,'scalar_training_comparisons':checks,'archive_blocks':len(manifest),
        'limitation':'All selected training seeds verified; all non-surviving candidates were not independently rescanned.'}
    save(out/'verification.json',result); print(json.dumps(result))


if __name__=='__main__': main()
