"""Alternate serial/parallel runs at identical budgets, verify full result hashes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=root/".local-build/parallel-benchmark")
    parser.add_argument("--candidates-per-half",type=int,default=1000000)
    parser.add_argument("--baseline-repeats",type=int,default=10000)
    parser.add_argument("--repetitions",type=int,default=2)
    args=parser.parse_args()
    if min(args.candidates_per_half,args.baseline_repeats,args.repetitions)<1:
        parser.error("positive budgets required")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("use a new benchmark directory")
    args.output.mkdir(parents=True,exist_ok=True)
    runs=[]
    expected=None
    for repetition in range(args.repetitions):
        for workers in ((1,2) if repetition%2==0 else (2,1)):
            directory=args.output/f"run-{len(runs)+1}-workers-{workers}"
            command=[sys.executable,str(root/"src/lotto_seed_128.py"),"--output",str(directory),
                     "--candidates-per-half",str(args.candidates_per_half),
                     "--baseline-repeats",str(args.baseline_repeats),"--workers",str(workers)]
            print(f"starting run {len(runs)+1}: workers={workers}",flush=True)
            started=time.perf_counter()
            with (args.output/f"run-{len(runs)+1}.log").open("w",encoding="utf-8") as log:
                subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
            wall=time.perf_counter()-started
            result=json.loads((directory/"experiment.json").read_text())
            scientific={k:result[k] for k in ("selection","validation","final")}
            digest=hashlib.sha256(json.dumps(scientific,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            if expected is not None and digest!=expected:
                raise AssertionError("serial/parallel scientific results differ")
            expected=digest
            runs.append({"workers":workers,"wall_seconds":wall,
                         "stage_seconds":result["timings_seconds"],"scientific_result_sha256":digest,
                         "selection_hash":result["selection_hash"],"pairs":len(result["selection"]["pairs"]),
                         "source_hashes":result["protocol"]["source_hashes"]})
            print(f"completed workers={workers}: {wall:.3f}s, results match",flush=True)
    serial=statistics.median(r["wall_seconds"] for r in runs if r["workers"]==1)
    parallel=statistics.median(r["wall_seconds"] for r in runs if r["workers"]==2)
    summary={"python":platform.python_version(),"platform":platform.platform(),"logical_cpu_count":os.cpu_count(),
             "candidates_per_half":args.candidates_per_half,"baseline_repeats":args.baseline_repeats,
             "runs":runs,"serial_median_seconds":serial,"parallel_median_seconds":parallel,
             "speedup":serial/parallel,"time_reduction_percent":100*(1-parallel/serial),
             "all_scientific_results_identical":True,
             "limitations":"Local alternating-order microbenchmark, two independent processes; not a hardware-independent speed guarantee."}
    (args.output/"benchmark.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps({k:summary[k] for k in ("serial_median_seconds","parallel_median_seconds","speedup","time_reduction_percent")}),flush=True)


if __name__=="__main__":
    main()
