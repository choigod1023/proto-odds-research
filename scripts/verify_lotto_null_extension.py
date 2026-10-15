"""Independent scalar hit checks plus archived-prefix and summary verification."""
import argparse
import gzip
import json
import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import load_draws_jsonl,data_hash
from lotto_bias_null import simulate,summarize_seeds
from lotto_null_extension import canonical_hash,tail_precision
from lotto_seed_128 import save

MASK=(1<<64)-1; N=math.comb(45,6); STEP=0x9E3779B97F4A7C15


def scalar_rank(seed,draw):
    x=(seed+draw*STEP)&MASK
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&MASK
    x=((x^(x>>27))*0x94D049BB133111EB)&MASK
    return (x^(x>>31))%N


def scalar_ticket(seed,draw):
    rest=scalar_rank(seed,draw); numbers=[]; upper=44
    for k in range(6,0,-1):
        while math.comb(upper,k)>rest: upper-=1
        numbers.append(upper+1); rest-=math.comb(upper,k); upper-=1
    assert rest==0
    return set(numbers)


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=root/'data/lotto/reports/null-expanded')
    out=parser.parse_args().report
    protocol=json.loads((out/'protocol.json').read_text())
    with gzip.open(out/'histories.jsonl.gz','rt',encoding='utf-8') as f: histories=[json.loads(line) for line in f]
    assert [r['history_index'] for r in histories]==list(range(protocol['total_fake_histories']+1))
    prefix=json.loads((out/'prefix-manifest.json').read_text())
    for i,digest in prefix['history_canonical_sha256'].items(): assert canonical_hash(histories[int(i)])==digest
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl'); assert data_hash(draws)==protocol['ledger_hash']
    actual=np.array([d.numbers_sorted for d in draws])-1
    comparisons=0; seeds=0
    for index,result in enumerate(histories):
        numbers=actual if index==0 else simulate(np.random.default_rng(645000+index),len(draws))
        targets=[sum(math.comb(int(n),k) for k,n in enumerate(ns,1)) for ns in numbers[:740]]
        for key,parity in [('odd',1),('even',0)]:
            half=result[key]; hist=half['histogram']; observed=[0]*len(hist)
            assert sum(hist)==1000000 and hist[0]==0
            for row in half['survivors']:
                hits=sum(scalar_rank(int(row['seed']),i+1)==target for i,target in enumerate(targets) if (i+1)%2==parity)
                assert hits==row['hits'] and hits>=2
                observed[hits]+=1; seeds+=1; comparisons+=370
            assert hist[2:]==observed[2:]
    summary=summarize_seeds(histories)
    for label in ('validation','final'):
        summary[label]['monte_carlo_precision']=tail_precision(histories[0][label]['z_against_matched_controls'],
            [r[label]['z_against_matched_controls'] for r in histories[1:] if r[label]['status']=='evaluated'])
    assert summary==json.loads((out/'seed-summary.json').read_text())
    new=load_draws_jsonl(out/'new-rounds/draws.jsonl')
    evaluated=json.loads((out/'new-rounds/evaluation.json').read_text())
    assert data_hash(new)==evaluated['new_ledger_hash']
    odd=[int(r['seed']) for r in histories[0]['odd']['survivors']]
    even=[int(r['seed']) for r in histories[0]['even']['survivors']]
    histogram=[0]*7; tickets=0
    for d in new:
        for o in odd:
            for e in even:
                hits=len(scalar_ticket(o if d.draw_no%2 else e,d.draw_no)&set(d.numbers_sorted))
                histogram[hits]+=1; tickets+=1
    assert histogram==evaluated['selected_histogram']
    assert sum(i*n for i,n in enumerate(histogram))/tickets==evaluated['mean']
    result={'status':'passed','histories':len(histories),'prefix_records_unchanged':len(prefix['history_canonical_sha256']),
        'scalar_half_seeds_checked':seeds,'scalar_training_comparisons':comparisons,
        'new_round_full_pair_tickets_checked':tickets,
        'limitation':'All selected survivors checked; non-survivor full native search not rerun independently.'}
    save(out/'verification.json',result); print(json.dumps(result))


if __name__=='__main__': main()
