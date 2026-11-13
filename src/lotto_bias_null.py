"""Fixed retrospective bias and complete seed-selection null experiments."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
from lotto645 import data_hash, load_draws_jsonl
from lotto_seed_expanded import MASK, N, combo_rank, ranks, uniform_controls
from lotto_seed_128 import half_histograms, save


def simulate(rng,n):
    return np.sort(np.argpartition(rng.random((n,45)),5,axis=1)[:,:6],axis=1)


def indicator(numbers):
    x=np.zeros((len(numbers),45),dtype=np.int64)
    x[np.arange(len(numbers))[:,None],numbers]=1
    return x


def bias_metrics(numbers,train_end=740,val_end=990):
    x=indicator(numbers); n=len(x); counts=x.sum(axis=0)
    metrics={'number_dispersion':float(((counts-n*6/45)**2).sum())}
    pairs=np.zeros((45,45),dtype=np.int64)
    for i in range(6):
        for j in range(i+1,6):
            np.add.at(pairs,(numbers[:,i],numbers[:,j]),1)
    pc=pairs[np.triu_indices(45,1)]
    metrics['pair_dispersion']=float(((pc-n/66)**2).sum())
    metrics['serial_overlap_deviation']=float(abs((x[1:]*x[:-1]).sum(axis=1).mean()-.8))
    corr=float(np.corrcoef(x[:train_end].sum(axis=0),x[val_end:].sum(axis=0))[0,1])
    metrics['frequency_persistence_abs']=abs(corr) if np.isfinite(corr) else 0.
    predictions={}
    # Fixed four models, trained before either evaluation interval.
    for window in (100,train_end):
        frequency=x[train_end-window:train_end].sum(axis=0)
        for mode in ('hot','cold'):
            order=np.argsort(-frequency if mode=='hot' else frequency,kind='stable')
            ticket=order[:6]
            for label,rows in [('validation',x[train_end:val_end]),('final',x[val_end:])]:
                key=f'{mode}_{window}_{label}'
                matches=rows[:,ticket].sum(axis=1)
                metrics[key]=float(matches.mean())
                predictions[key]={'ticket':(ticket+1).tolist(),'mean':float(matches.mean()),
                    'jackpots':int((matches==6).sum()),'draws':len(rows)}
    return metrics,{'frequency_persistence_signed':corr,'predictions':predictions}


def holm(pvalues):
    p=np.asarray(pvalues); order=np.argsort(p); out=np.empty(len(p)); running=0.
    for i,index in enumerate(order):
        running=max(running,(len(p)-i)*float(p[index])); out[index]=min(1.,running)
    return out.tolist()


def bias_experiment(numbers,repeats,seed):
    observed,detail=bias_metrics(numbers)
    rng=np.random.default_rng(seed); names=list(observed); null=[]
    for i in range(repeats):
        stats,_=bias_metrics(simulate(rng,len(numbers)))
        null.append([stats[k] for k in names])
    null=np.array(null)
    ps=[float((1+(null[:,j]>=observed[k]).sum())/(repeats+1)) for j,k in enumerate(names)]
    adjusted=holm(ps)
    return {'detail':detail,'tests':{k:{'observed':observed[k],'p_mc':ps[j],'p_holm':adjusted[j],
        'null_interval_95':np.quantile(null[:,j],[.025,.975]).tolist()} for j,k in enumerate(names)},
        'null_repeats':repeats,'multiplicity_family_size':len(names)}


def native_half(executable,rows,count):
    target=combo_rank(rows[0].numbers_sorted); population=(MASK-target)//N+1
    stride=1000003
    while math.gcd(stride,population)!=1:
        stride+=1
    payload=f'{len(rows)} {rows[0].draw_no} {target} 0 {count} 20261015 {stride} 1\n'
    payload+=''.join(f'{d.draw_no} {combo_rank(d.numbers_sorted)}\n' for d in rows)
    batch=json.loads(subprocess.run([str(executable)],input=payload,text=True,capture_output=True,check=True).stdout)
    hist=batch['histogram']
    if len(hist)!=len(rows)+1 or sum(hist)!=count or hist[0] or sum(hist[2:])!=len(batch['survivors']):
        raise ValueError('native histogram mismatch; compile with MIN_HITS=2')
    seeds=np.array([int(r['seed']) for r in batch['survivors']],dtype=np.uint64)
    if len(set(map(int,seeds)))!=len(seeds):
        raise ValueError('duplicate survivors')
    hits=np.zeros(len(seeds),dtype=int)
    for d in rows:
        hits+=ranks(seeds,d.draw_no)==combo_rank(d.numbers_sorted)
    if hits.tolist()!=[r['hits'] for r in batch['survivors']]:
        raise ValueError('Python/native hit mismatch')
    return seeds,{'histogram':hist,'survivors':batch['survivors'],'stride':stride}


def compact_evaluate(odd,even,oc,ec,rows):
    a,b=len(odd),len(even)
    if not a or not b:
        return {'status':'no_survivors'}
    r=len(oc); oddrows=[d for d in rows if d.draw_no%2]; evenrows=[d for d in rows if not d.draw_no%2]
    oh=half_histograms(np.concatenate([odd,oc.ravel()]),oddrows)
    eh=half_histograms(np.concatenate([even,ec.ravel()]),evenrows)
    selected=b*oh[:a].sum(axis=0)+a*eh[:b].sum(axis=0)
    controls=b*oh[a:].reshape(r,a,7).sum(axis=1)+a*eh[b:].reshape(r,b,7).sum(axis=1)
    mean=float(selected@np.arange(7)/(a*b*len(rows)))
    cm=controls@np.arange(7)/(a*b*len(rows)); sd=float(cm.std(ddof=1))
    return {'status':'evaluated','mean':mean,'jackpots':int(selected[6]),
        'control_mean':float(cm.mean()),'difference':mean-float(cm.mean()),
        'control_interval_95':np.quantile(cm,[.025,.975]).tolist(),
        'p_mc':float((1+(cm>=mean).sum())/(r+1)),
        'z_against_matched_controls':(mean-float(cm.mean()))/sd if sd else 0.,
        'selected_histogram':selected.tolist(),'control_means':cm.tolist()}


def seed_history(index,draws,executable,candidates,controls):
    if index:
        numbers=simulate(np.random.default_rng(645000+index),len(draws))
        draws=[replace(d,numbers_sorted=tuple(map(int,ns+1)),bonus_number=next(n for n in range(1,46) if n not in ns+1)) for d,ns in zip(draws,numbers)]
    train=draws[:740]
    odd,od=native_half(executable,[d for d in train if d.draw_no%2],candidates)
    even,ed=native_half(executable,[d for d in train if not d.draw_no%2],candidates)
    # The same random control seeds are used across histories; no cherry-picking.
    oc=uniform_controls(odd,controls,12820261015); ec=uniform_controls(even,controls,12820261016)
    result={'history_index':index,'kind':'actual' if index==0 else 'synthetic',
        'odd':od,'even':ed,'pair_count':len(odd)*len(even),
        'maximum_training_hits':max(i for i,v in enumerate(od['histogram']) if v)+max(i for i,v in enumerate(ed['histogram']) if v),
        'validation':compact_evaluate(odd,even,oc,ec,draws[740:990]),
        'final':compact_evaluate(odd,even,oc,ec,draws[990:])}
    print(f'history {index}: pairs={result["pair_count"]}; max training hits={result["maximum_training_hits"]}',flush=True)
    return result


def summarize_seeds(rows):
    actual=rows[0]; fake=rows[1:]
    result={'synthetic_histories':len(fake),'synthetic_with_pairs':sum(r['pair_count']>0 for r in fake),
        'actual_pairs':actual['pair_count'],'actual_max_training_hits':actual['maximum_training_hits'],
        'synthetic_pairs_interval_95':np.quantile([r['pair_count'] for r in fake],[.025,.975]).tolist(),
        'synthetic_max_training_hits_histogram':{str(k):sum(r['maximum_training_hits']==k for r in fake) for k in sorted({r['maximum_training_hits'] for r in fake})}}
    for label in ('validation','final'):
        available=[r[label] for r in fake if r[label]['status']=='evaluated']
        if actual[label]['status']!='evaluated' or not available:
            result[label]={'status':'no_comparison'}; continue
        z=actual[label]['z_against_matched_controls']
        p=(1+sum(r['z_against_matched_controls']>=z for r in available))/(len(available)+1)
        result[label]={'actual':actual[label],'synthetic_mean_average':float(np.mean([r['mean'] for r in available])),
            'synthetic_mean_interval_95':np.quantile([r['mean'] for r in available],[.025,.975]).tolist(),
            'actual_vs_synthetic_z_p_mc':p,'p_bonferroni_two_splits':min(1.,2*p),
            'available_histories':len(available)}
    return result


def main():
    root=Path(__file__).resolve().parents[1]; p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--executable',type=Path,required=True)
    p.add_argument('--output',type=Path,default=root/'data/lotto/reports/bias-null')
    p.add_argument('--bias-repeats',type=int,default=1999)
    p.add_argument('--fake-histories',type=int,default=99)
    p.add_argument('--candidates',type=int,default=1000000)
    p.add_argument('--controls',type=int,default=199)
    a=p.parse_args(); draws=load_draws_jsonl(root/'data/lotto/draws.jsonl')
    if len(draws)<=990 or draws[0].draw_no!=1 or min(a.bias_repeats,a.fake_histories,a.candidates)<1 or a.controls<2:
        p.error('invalid data or budget')
    if a.output.exists() and any(a.output.iterdir()):
        p.error('use a new output directory')
    a.output.mkdir(parents=True)
    protocol={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}
    protocol.update(training=[1,740],validation=[741,990],final=[991,len(draws)],ledger_hash=data_hash(draws),
        generator='splitmix64-paired128-v1',threshold='2+2 training hits, including two constructed anchors',
        bias_seed=20261015,history_seed='645000+index',workers=4,
        caveat='retrospective, previously inspected holdouts; reduced-budget full selection null, not a repeat of the seven-hit budget',
        bias_tests='4 audit statistics plus 4 fixed prediction models x 2 splits; Holm correction over all 12',
        seed_tests='matched-sharing control z compared with synthetic-history z; Bonferroni over 2 splits',
        source_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        executable_hash=hashlib.sha256(a.executable.read_bytes()).hexdigest())
    save(a.output/'protocol.json',protocol)
    start=time.perf_counter()
    numbers=np.array([d.numbers_sorted for d in draws])-1
    bias=bias_experiment(numbers,a.bias_repeats,20261015); save(a.output/'bias.json',bias)
    print('bias experiment complete',flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(seed_history,i,draws,a.executable.resolve(),a.candidates,a.controls) for i in range(a.fake_histories+1)]
        rows=[]
        for i,f in enumerate(futures):
            row=f.result(); save(a.output/f'history-{i:03d}.json',row); rows.append(row)
    summary=summarize_seeds(rows); save(a.output/'seed-summary.json',summary)
    save(a.output/'timing.json',{'total_seconds':time.perf_counter()-start})
    print(json.dumps({'bias_min_holm':min(r['p_holm'] for r in bias['tests'].values()),
                      'actual_pairs':summary['actual_pairs'],'synthetic_with_pairs':summary['synthetic_with_pairs']}))


if __name__=='__main__':
    main()
