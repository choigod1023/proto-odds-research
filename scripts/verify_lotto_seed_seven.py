"""Independent scalar verification of batch coverage, seed membership and hits."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import data_hash, load_draws_jsonl

MASK=(1<<64)-1
N=math.comb(45,6)
STEP=0x9E3779B97F4A7C15


def matches_digest(raw, expected):
    # Git may convert LF/CRLF on checkout; preserve the original run hashes.
    lf=raw.replace(b'\r\n',b'\n')
    return any(hashlib.sha256(v).hexdigest()==expected for v in (raw,lf,lf.replace(b'\n',b'\r\n')))


def mix(x):
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&MASK
    x=((x^(x>>27))*0x94D049BB133111EB)&MASK
    return x^(x>>31)


def rank(numbers):
    return sum(math.comb(n-1,k) for k,n in enumerate(sorted(numbers),1))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[1]
    p.add_argument('--report',type=Path,default=root/'data/lotto/reports/seed-seven')
    a=p.parse_args()
    protocol=json.loads((a.report/'protocol.json').read_text())
    selected=json.loads((a.report/'selection.json').read_text())
    result=json.loads((a.report/'experiment.json').read_text())
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    assert data_hash(draws)==protocol['ledger_hash']
    assert matches_digest((root/'src/lotto_seed_seven.py').read_bytes(),protocol['source_sha256'])
    summary={}
    for label,parity in [('odd',1),('even',0)]:
        training=[d for d in draws if d.draw_no<=protocol['train_end'] and d.draw_no%2==parity]
        half=selected['halves'][label]
        hist=[0]*(len(training)+1)
        cursor=0
        survivors={}
        target=rank(training[0].numbers_sorted)
        population=(MASK-target)//N+1
        inverse=pow(protocol['stride'],-1,population)
        for entry in half['manifest']:
            raw=(a.report/entry['file']).read_bytes()
            assert matches_digest(raw,entry['sha256'])
            batch=json.loads(raw)
            assert batch['start']==cursor and sum(batch['histogram'])==batch['count']
            assert len(batch['histogram'])==len(hist) and all(v>=0 for v in batch['histogram'])
            actual={}
            for item in batch['survivors']:
                seed=int(item['seed'])
                assert seed not in survivors
                x=mix((seed+training[0].draw_no*STEP)&MASK)
                assert x%N==target
                q=(x-target)//N
                index=((q-protocol['offset'])*inverse)%population
                assert cursor<=index<cursor+batch['count']
                hits=[d.draw_no for d in training if mix((seed+d.draw_no*STEP)&MASK)%N==rank(d.numbers_sorted)]
                assert len(hits)==item['hits'] and len(hits)>=3
                actual[len(hits)]=actual.get(len(hits),0)+1
                survivors[seed]=hits
            assert all(actual.get(i,0)==batch['histogram'][i] for i in range(3,len(hist)))
            hist=[x+y for x,y in zip(hist,batch['histogram'])]
            cursor+=batch['count']
        assert cursor==protocol['candidates_per_half'] and hist==half['histogram']
        assert {int(s['seed']) for s in half['survivors']}==set(survivors)
        summary[label]={'candidates':cursor,'maximum_hits':max(i for i,v in enumerate(hist) if v),
            'survival_counts':{str(k):sum(hist[k:]) for k in range(1,5)},
            'verified_survivor_hit_draws':survivors}
    maximum=sum(s['maximum_hits'] for s in summary.values())
    assert maximum==result['maximum_pair_hits_in_entire_scanned_product']
    if maximum<7:
        assert not selected['pairs'] and not result['results']
        assert result['evaluation_status']=='not_applicable_no_qualifying_seed'
    output={'status':'passed','halves':summary,'maximum_pair_hits':maximum,
        'limitation':'Non-survivor histogram counts are native scan outputs, not an independent rerun of 8 billion halves.'}
    (a.report/'verification.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','maximum_pair_hits':maximum,'survivors':{k:len(v['verified_survivor_hit_draws']) for k,v in summary.items()}}))


if __name__=='__main__':
    main()
