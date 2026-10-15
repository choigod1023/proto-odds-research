"""Extend an audited paired-128 search without repeating its prefix.

Selection uses training only. Seven-hit groups require >=3 per half and >=7
in total; groups are Cartesian products of exact training hit counts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np

from lotto645 import data_hash, load_draws_jsonl, validate_draws
from lotto_seed_expanded import MASK, N, combo_rank, ranks, uniform_controls
from lotto_seed_128 import VERSION, encode_seed, evaluate_splits, save


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_batch(batch, training, start, count):
    hist = batch['histogram']
    if (batch['start'] != start or batch['count'] != count or
        len(hist) != len(training)+1 or any(type(v) is not int or v < 0 for v in hist) or
        sum(hist) != count or hist[0] or sum(hist[3:]) != len(batch['survivors'])):
        raise ValueError('invalid batch coverage or histogram')
    observed = [0]*len(hist)
    seeds = set()
    for row in batch['survivors']:
        seed = int(row['seed'])
        if seed in seeds or not 0 <= seed <= MASK:
            raise ValueError('duplicate or invalid survivor')
        seeds.add(seed)
        hits = sum(int(ranks([seed], d.draw_no)[0]) == combo_rank(d.numbers_sorted) for d in training)
        if hits != row['hits'] or hits < 3:
            raise ValueError('native/Python hit mismatch')
        observed[hits] += 1
    if observed[3:] != hist[3:]:
        raise ValueError('survivor histogram mismatch')


def extend_half(label, training, prior, output, old_count, total, batch_size, executable, offset, stride, threads):
    histogram = np.zeros(len(training)+1, dtype=np.int64)
    survivors, manifest = [], []
    target = combo_rank(training[0].numbers_sorted)
    population = (MASK-target)//N+1
    if not (0 < old_count < total <= population and old_count % batch_size == 0 and
            0 <= offset < population and 0 < stride < population and math.gcd(stride,population)==1):
        raise ValueError('invalid extension/permutation')
    for start in range(0, total, batch_size):
        count = min(batch_size,total-start)
        name = f'{label}-batch-{start:012d}.json'
        dest = output/name
        if start < old_count:
            source = prior/name
            batch = json.loads(source.read_text())
            check_batch(batch,training,start,count)
            shutil.copyfile(source,dest)
        else:
            payload = f'{len(training)} {training[0].draw_no} {target} {start} {count} {offset} {stride} {threads}\n'
            payload += ''.join(f'{d.draw_no} {combo_rank(d.numbers_sorted)}\n' for d in training)
            begin = time.perf_counter()
            result = subprocess.run([str(executable)],input=payload,text=True,capture_output=True,check=True)
            batch = json.loads(result.stdout)
            batch.update(start=start,count=count,seconds=time.perf_counter()-begin)
            check_batch(batch,training,start,count)
            save(dest,batch)
            print(f'{label}: {start+count:,}/{total:,}; batch >=3 hits={len(batch["survivors"])}',flush=True)
        manifest.append({'file':name,'sha256':digest(dest),'reused':start<old_count})
        histogram += batch['histogram']
        survivors.extend(batch['survivors'])
    if len({r['seed'] for r in survivors}) != len(survivors):
        raise ValueError('duplicate survivors across batches')
    return {'histogram':histogram.tolist(),'survivors':survivors,'manifest':manifest}


def groups_for_seven(odd, even):
    groups = []
    for oh in sorted({r['hits'] for r in odd}):
        for eh in sorted({r['hits'] for r in even}):
            if oh >= 3 and eh >= 3 and oh+eh >= 7:
                groups.append({'odd_hits':oh,'even_hits':eh,
                    'odd':[int(r['seed']) for r in odd if r['hits']==oh],
                    'even':[int(r['seed']) for r in even if r['hits']==eh]})
    return groups


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior',type=Path,required=True)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--total',type=int,default=4_000_000_000)
    p.add_argument('--output',type=Path,default=root/'data/lotto/reports/seed-seven')
    a=p.parse_args()
    old=json.loads((a.prior/'protocol.json').read_text())
    draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    validate_draws(draws)
    train=[d for d in draws if d.draw_no<=old['train_end']]
    if (old['version']!=VERSION or old['ledger_hash']!=data_hash(draws) or
        old['training_hash']!=data_hash(train) or old['min_odd_wins']!=3 or old['min_even_wins']!=3 or
        old['odd_sampler']!='affine permutation of first-odd-draw preimages' or
        old['even_sampler']!='affine permutation of first-even-draw preimages'):
        raise ValueError('incompatible prefix protocol')
    for name in ('lotto_seed_scan.cpp','lotto_seed_expanded.py','lotto_seed_128.py'):
        if digest(root/'src'/name)!=old['source_hashes'][name]:
            raise ValueError('generator/scanner source changed')
    if a.output.exists() and any(a.output.iterdir()):
        raise ValueError('use an empty output directory')
    a.output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(a.prior/'protocol.json',a.output/'prefix-protocol.json')
    protocol={'version':VERSION,'minimum_pair_wins':7,'minimum_half_wins':3,
        'candidates_per_half':a.total,'new_candidates':2*a.total-old['odd_candidates']-old['even_candidates'],
        'train_end':old['train_end'],'validation_end':old['validation_end'],'last_draw':draws[-1].draw_no,
        'ledger_hash':data_hash(draws),'prefix_protocol_sha256':digest(a.output/'prefix-protocol.json'),
        'offset':old['offset'],'stride':old['stride'],'workers':2,'native_threads_per_worker':4,
        'executable_sha256':digest(a.executable),'source_sha256':digest(__file__),
        'selection':'exact-hit Cartesian groups with >=3 hits per half and >=7 total',
        'controls':'10000 matched-sharing groups per selected group; Bonferroni across groups',
        'holdout_status':'previously inspected retrospective exploration, not a fresh prospective test'}
    save(a.output/'protocol.json',protocol)
    begin=time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={label:pool.submit(extend_half,label,[d for d in train if d.draw_no%2==parity],
            a.prior,a.output,old[f'{label}_candidates'],a.total,old['batch_size'],a.executable.resolve(),
            old['offset'],old['stride'],4) for label,parity in [('odd',1),('even',0)]}
        halves={k:f.result() for k,f in futures.items()}
    groups=groups_for_seven(halves['odd']['survivors'],halves['even']['survivors'])
    pairs=[{'seed_hex':encode_seed(o,e),'training_hits':g['odd_hits']+g['even_hits']}
           for g in groups for o in g['odd'] for e in g['even']]
    selection={'halves':halves,'groups':groups,'pairs':pairs}
    save(a.output/'selection.json',selection)
    results=[]
    # Selection is saved before any holdout evaluation. Empty sets have no score.
    for g in groups:
        odd=np.array(g['odd'],dtype=np.uint64); even=np.array(g['even'],dtype=np.uint64)
        oc=uniform_controls(odd,10000,12820261015); ec=uniform_controls(even,10000,12820261016)
        evaluation=evaluate_splits(odd,even,oc,ec,
            [d for d in draws if old['train_end']<d.draw_no<=old['validation_end']],
            [d for d in draws if d.draw_no>old['validation_end']],workers=1)
        results.append({'group':g,'evaluation':evaluation,'multiplicity_factor':len(groups)})
    maximum=sum(max(i for i,v in enumerate(halves[k]['histogram']) if v) for k in ('odd','even'))
    result={'protocol':protocol,'selection_seconds':time.perf_counter()-begin,'selected_pairs':len(pairs),
        'maximum_pair_hits_in_entire_scanned_product':maximum,
        'evaluation_status':'evaluated_by_group' if groups else 'not_applicable_no_qualifying_seed',
        'results':results,
        'four_hit_half_probability_independent_approx':math.comb(len(train)//2-1,3)/N**3}
    save(a.output/'experiment.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('protocol','results')},indent=2))


if __name__=='__main__':
    main()
