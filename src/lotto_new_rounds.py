"""Evaluate frozen actual survivors on official, previously unused rounds."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from lotto645 import load_draws_jsonl,save_draws_jsonl,validate_draws,data_hash
from lotto_collect import parse_official_item,OFFICIAL_ENDPOINT
from lotto_bias_null import compact_evaluate
from lotto_seed_expanded import uniform_controls
from lotto_seed_128 import half_histograms,save


def validate_extension(old,new,first=1239,last=1245):
    validate_draws(new)
    if [d.draw_no for d in new]!=list(range(first,last+1)):
        raise ValueError('complete predeclared new-round range required')
    if any(d.draw_no<=old[-1].draw_no for d in new):
        raise ValueError('new evaluation overlaps existing ledger')
    if any(d.draw_date>'2026-10-16' for d in new):
        raise ValueError('future-dated outcomes prohibited')


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=root/'data/lotto/reports/null-expanded')
    out=parser.parse_args().report
    plan=json.loads((out/'protocol.json').read_text())['new_rounds_plan']
    actual=json.loads((out/'actual-frozen.json').read_text())
    old=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    source=out/'new-rounds/official-response.txt'; raw=source.read_bytes()
    url=OFFICIAL_ENDPOINT+'?srchDir=center&srchLtEpsd=1245'
    batch=[parse_official_item(item,source_url=url,collected_at='2026-10-16')
           for item in json.loads(raw)['data']['list']]
    for d in batch:
        if d.draw_no<=1238:
            previous=old[d.draw_no-1]
            if (previous.numbers_sorted,previous.bonus_number,previous.draw_date)!=(d.numbers_sorted,d.bonus_number,d.draw_date):
                raise ValueError('official overlap differs from original ledger')
    new=sorted([d for d in batch if plan['first']<=d.draw_no<=plan['last_cap']],key=lambda d:d.draw_no)
    validate_extension(old,new,plan['first'],plan['last_cap'])
    save_draws_jsonl(new,out/'new-rounds/draws.jsonl')
    odd=np.array([int(r['seed']) for r in actual['odd']['survivors']],dtype=np.uint64)
    even=np.array([int(r['seed']) for r in actual['even']['survivors']],dtype=np.uint64)
    oc=uniform_controls(odd,plan['controls'],plan['odd_control_seed'])
    ec=uniform_controls(even,plan['controls'],plan['even_control_seed'])
    result=compact_evaluate(odd,even,oc,ec,new)
    per_draw=[]
    for d in new:
        halves=odd if d.draw_no%2 else even
        hist=half_histograms(halves,[d]).sum(axis=0)
        per_draw.append({'draw':d.draw_no,'date':d.draw_date,'numbers':list(d.numbers_sorted),
            'selected_mean_matches':float(hist@np.arange(7)/len(halves)),
            'distinct_half_histogram':hist.tolist()})
    result.update(draw_range=[new[0].draw_no,new[-1].draw_no],draw_count=len(new),pair_count=len(odd)*len(even),
        odd_half_count=len(odd),even_half_count=len(even),
        distinct_stream_tickets=sum(len(odd) if d.draw_no%2 else len(even) for d in new),
        controls=plan['controls'],source_url=url,response_sha256=hashlib.sha256(raw).hexdigest(),
        new_ledger_hash=data_hash(new),per_draw=per_draw,
        caveat='seven previously unused but already published rounds; frozen rule, not a prospective trial; shared halves are not independent tickets')
    save(out/'new-rounds/evaluation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('per_draw','control_means','selected_histogram')},indent=2))


if __name__=='__main__': main()
