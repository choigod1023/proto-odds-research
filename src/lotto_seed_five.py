"""Fixed-budget 3+2 hit search with the existing paired-128 generator."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import platform
import subprocess
import time

import numpy as np

from lotto645 import data_hash, load_draws_jsonl, sha256_json
from lotto_seed_expanded import MASK, N, combo_rank, ranks, select, uniform_controls
from lotto_seed_128 import VERSION, encode_seed, evaluate_splits, markdown, save


def scan_half(executable, training, count, batch_size, offset, stride, threads, output=None):
    if not training or any(d.draw_no % 2 != training[0].draw_no % 2 for d in training):
        raise ValueError("training rounds of a single parity required")
    label="odd" if training[0].draw_no%2 else "even"
    target=combo_rank(training[0].numbers_sorted)
    population=(MASK-target)//N+1
    if not (0<count<=population and batch_size>0 and 0<=offset<population and
            0<stride<population and math.gcd(stride,population)==1 and 1<=threads<=64):
        raise ValueError("invalid native search configuration")
    histogram=np.zeros(len(training)+1,dtype=np.int64)
    survivors=[]
    for start in range(0,count,batch_size):
        size=min(batch_size,count-start)
        payload=f"{len(training)} {training[0].draw_no} {target} {start} {size} {offset} {stride} {threads}\n"
        payload+="".join(f"{d.draw_no} {combo_rank(d.numbers_sorted)}\n" for d in training)
        begin=time.perf_counter()
        completed=subprocess.run([str(executable)],input=payload,text=True,capture_output=True,check=True)
        batch=json.loads(completed.stdout)
        if (len(batch["histogram"])!=len(training)+1 or sum(batch["histogram"])!=size or
            batch["histogram"][0] or sum(batch["histogram"][3:])!=len(batch["survivors"])):
            raise ValueError("native histogram or anchor mismatch")
        batch.update(start=start,count=size,seconds=time.perf_counter()-begin)
        if output is not None:
            save(Path(output)/f"{label}-batch-{start:012d}.json",batch)
        histogram+=np.array(batch["histogram"],dtype=np.int64)
        survivors.extend(batch["survivors"])
        print(f"{label} scanned={start+size:,}/{count:,}; three-or-more={len(survivors)}",flush=True)
    selected=np.array(sorted(int(row["seed"]) for row in survivors),dtype=np.uint64)
    if len(set(map(int,selected)))!=len(selected):
        raise ValueError("duplicate survivor")
    counts={str(k):int(histogram[k:].sum()) for k in range(1,max(4,int(np.flatnonzero(histogram)[-1]))+1)}
    # Recheck every native survivor in Python before any evaluation.
    expected={row["seed"]:row["hits"] for row in survivors}
    for seed in selected:
        hits=sum(int(ranks([seed],d.draw_no)[0])==combo_rank(d.numbers_sorted) for d in training)
        if hits<3 or hits!=expected[str(int(seed))]:
            raise ValueError("native/Python survivor mismatch")
    return selected,counts


def scan_odd(executable, training, count, batch_size, offset, stride, threads, output=None):
    if not training or any(d.draw_no % 2 == 0 for d in training):
        raise ValueError("odd training rounds required")
    return scan_half(executable, training, count, batch_size, offset, stride, threads, output)


def build_selection(odd,even,training,odd_counts,even_counts,minimum_even=2):
    if minimum_even not in (2,3):
        raise ValueError("supported even thresholds are 2 and 3")
    def describe(seeds,parity,minimum):
        rows=[]
        for seed in np.sort(seeds):
            hits=[d.draw_no for d in training if d.draw_no%2==parity and
                  int(ranks([seed],d.draw_no)[0])==combo_rank(d.numbers_sorted)]
            if len(hits)<minimum:
                raise ValueError("half does not meet the predeclared threshold")
            rows.append({"half_seed":str(int(seed)),"draws":hits})
        return rows
    oh,eh=describe(odd,1,3),describe(even,0,minimum_even)
    pairs=[{"seed_hex":encode_seed(o["half_seed"],e["half_seed"]),
            "training_jackpot_draws":sorted(o["draws"]+e["draws"])} for o in oh for e in eh]
    return {"odd_halves":oh,"even_halves":eh,"pairs":pairs,
            "odd_survival_counts":odd_counts,"even_survival_counts":even_counts,
            "minimum_odd_training_hits":3,"minimum_even_training_hits":minimum_even,
            "pairing":f"all Cartesian pairs, training-only 3+{minimum_even} threshold"}


def main(argv=None, *, minimum_even=2):
    if minimum_even not in (2,3):
        raise ValueError("supported even thresholds are 2 and 3")
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=f"Fixed-budget 3+{minimum_even} paired-128 search")
    parser.add_argument("--executable",type=Path,required=True)
    parser.add_argument("--data",type=Path,default=root/"data/lotto/draws.jsonl")
    parser.add_argument("--output",type=Path,default=root/"data/lotto/reports"/("seed-five" if minimum_even==2 else "seed-six"))
    parser.add_argument("--odd-candidates",type=int,default=1_000_000_000 if minimum_even==2 else 2_000_000_000)
    parser.add_argument("--even-candidates",type=int,default=2_000_000 if minimum_even==2 else 2_000_000_000)
    parser.add_argument("--batch-size",type=int,default=100_000_000)
    parser.add_argument("--native-threads",type=int,default=4)
    parser.add_argument("--workers",type=int,choices=(1,2),default=2)
    parser.add_argument("--offset",type=int,default=20261015)
    parser.add_argument("--stride",type=int,default=1000003)
    parser.add_argument("--even-search-seed",type=int,default=20261016)
    parser.add_argument("--odd-control-seed",type=int,default=12820261015)
    parser.add_argument("--even-control-seed",type=int,default=12820261016)
    parser.add_argument("--train-end",type=int,default=740)
    parser.add_argument("--validation-end",type=int,default=990)
    parser.add_argument("--baseline-repeats",type=int,default=10000)
    args=parser.parse_args(argv)
    draws=load_draws_jsonl(args.data)
    if not draws or not 0<draws[0].draw_no<args.train_end<args.validation_end<draws[-1].draw_no:
        parser.error("three chronological nonempty splits required")
    train=[d for d in draws if d.draw_no<=args.train_end]
    odd_draws=[d for d in train if d.draw_no%2]
    even_draws=[d for d in train if not d.draw_no%2]
    if len(odd_draws)<3 or len(even_draws)<minimum_even:
        parser.error(f"at least three odd and {minimum_even} even training rounds required")
    population=(MASK-combo_rank(odd_draws[0].numbers_sorted))//N+1
    even_population=(MASK-combo_rank(even_draws[0].numbers_sorted))//N+1
    if not (0<args.odd_candidates<=population and 0<args.even_candidates<=even_population and
            args.batch_size>0 and args.baseline_repeats>0 and 1<=args.native_threads<=64 and
            0<=args.offset<population and 0<args.stride<population and math.gcd(args.stride,population)==1):
        parser.error("invalid candidate, permutation, thread, or baseline configuration")
    if args.odd_control_seed==args.even_control_seed:
        parser.error("separate control random streams required")
    if minimum_even==3 and not (0<=args.offset<even_population and 0<args.stride<even_population and math.gcd(args.stride,even_population)==1):
        parser.error("invalid even affine permutation")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("use a new output directory")
    args.executable=args.executable.resolve()
    protocol={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}
    protocol.update(version=VERSION,first_draw=draws[0].draw_no,last_draw=draws[-1].draw_no,
                    min_odd_wins=3,min_even_wins=minimum_even,minimum_pair_wins=3+minimum_even,
                    odd_sampler="affine permutation of first-odd-draw preimages",
                    even_sampler=("uniform sample without replacement of first-even-draw preimages" if minimum_even==2 else "affine permutation of first-even-draw preimages"),
                    python=platform.python_version(),numpy=np.__version__,
                    training_hash=data_hash(train),ledger_hash=data_hash(draws),
                    executable_sha256=hashlib.sha256(args.executable.read_bytes()).hexdigest(),
                    source_hashes={n:hashlib.sha256((root/"src"/n).read_bytes()).hexdigest()
                                   for n in ("lotto_seed_five.py","lotto_seed_six.py","lotto_seed_scan.cpp","lotto_seed_128.py","lotto_seed_expanded.py")})
    args.output.mkdir(parents=True,exist_ok=True)
    save(args.output/"protocol.json",protocol)
    begin=time.perf_counter()
    odd_args=(args.executable,odd_draws,args.odd_candidates,args.batch_size,args.offset,args.stride,args.native_threads,args.output)
    even_args=(args.executable,even_draws,args.even_candidates,args.batch_size,args.offset,args.stride,args.native_threads,args.output)
    if args.workers==2:
        with ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context("spawn")) as pool:
            oj=pool.submit(scan_odd,*odd_args)
            ej=(pool.submit(scan_half,*even_args) if minimum_even==3 else
                pool.submit(select,even_draws,args.even_candidates,args.even_search_seed,progress=True))
            odd,odd_counts=oj.result()
            even,even_counts=ej.result()
    else:
        odd,odd_counts=scan_odd(*odd_args)
        even,even_counts=(scan_half(*even_args) if minimum_even==3 else
                         select(even_draws,args.even_candidates,args.even_search_seed,progress=True))
    odd,even=np.sort(odd),np.sort(even)
    selection=build_selection(odd,even,train,odd_counts,even_counts,minimum_even=minimum_even)
    selection_seconds=time.perf_counter()-begin
    oc=uniform_controls(odd,args.baseline_repeats,args.odd_control_seed)
    ec=uniform_controls(even,args.baseline_repeats,args.even_control_seed)
    selection["control_sha256"]=hashlib.sha256(oc.astype("<u8").tobytes()+ec.astype("<u8").tobytes()).hexdigest()
    save(args.output/"selection.json",selection)
    print(f"odd={len(odd)}; even={len(even)}; {3+minimum_even}-hit pairs={len(odd)*len(even)}; evaluating all",flush=True)
    eval_begin=time.perf_counter()
    evaluated=evaluate_splits(odd,even,oc,ec,
                             [d for d in draws if args.train_end<d.draw_no<=args.validation_end],
                             [d for d in draws if d.draw_no>args.validation_end],workers=args.workers)
    result={"protocol":protocol,"selection":selection,"selection_hash":sha256_json(selection),**evaluated,
            "timings_seconds":{"selection":selection_seconds,"evaluation":time.perf_counter()-eval_begin,
                               "computation_total":time.perf_counter()-begin}}
    save(args.output/"experiment.json",result)
    (args.output/"report.md").write_text(markdown(result),encoding="utf-8")
    print(args.output/"report.md",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
