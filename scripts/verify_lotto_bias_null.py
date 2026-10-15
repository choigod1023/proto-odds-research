"""Recompute null statistics and independently check all selected training seeds."""
import argparse
import json
import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import load_draws_jsonl
from lotto_bias_null import simulate,bias_experiment,summarize_seeds
from lotto_seed_128 import save

MASK=(1<<64)-1; N=math.comb(45,6); STEP=0x9E3779B97F4A7C15


def scalar_rank(seed,draw):
    x=(seed+draw*STEP)&MASK
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&MASK
    x=((x^(x>>27))*0x94D049BB133111EB)&MASK
    return (x^(x>>31))%N


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=root/'data/lotto/reports/bias-null')
    out=parser.parse_args().report
    protocol=json.loads((out/'protocol.json').read_text()); draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    actual=np.array([d.numbers_sorted for d in draws])-1
    bias=bias_experiment(actual,protocol['bias_repeats'],protocol['bias_seed'])
    assert bias==json.loads((out/'bias.json').read_text())
    histories=[]; survivors=0; comparisons=0
    for index in range(protocol['fake_histories']+1):
        result=json.loads((out/f'history-{index:03d}.json').read_text()); histories.append(result)
        numbers=actual if index==0 else simulate(np.random.default_rng(645000+index),len(draws))
        targets=[sum(math.comb(int(n),k) for k,n in enumerate(ns,1)) for ns in numbers[:740]]
        for key,parity in [('odd',1),('even',0)]:
            half=result[key]; hist=half['histogram']; observed=[0]*len(hist)
            assert sum(hist)==protocol['candidates'] and hist[0]==0
            assert len({r['seed'] for r in half['survivors']})==len(half['survivors'])
            for row in half['survivors']:
                hits=sum(scalar_rank(int(row['seed']),i+1)==target for i,target in enumerate(targets) if (i+1)%2==parity)
                assert hits==row['hits'] and hits>=2
                observed[hits]+=1; survivors+=1; comparisons+=370
            assert hist[2:]==observed[2:]
        assert result['pair_count']==len(result['odd']['survivors'])*len(result['even']['survivors'])
    assert summarize_seeds(histories)==json.loads((out/'seed-summary.json').read_text())
    result={'status':'passed','histories':len(histories),'scalar_checked_half_seeds':survivors,
        'scalar_training_comparisons':comparisons,'bias_mc_recomputed':protocol['bias_repeats'],
        'note':'Non-survivor native scan was not repeated at full scale; synthetic native/scalar equality is unit-tested.'}
    save(out/'verification.json',result); print(json.dumps(result))


if __name__=='__main__': main()
