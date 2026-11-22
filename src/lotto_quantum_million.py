"""Two low-priority workers; unchanged model and deterministic history IDs."""
import lotto_small_quantum as frozen
import lotto_quantum_99999 as previous
from lotto_quantum_9999 import canonical_hash,write_archive
from lotto_quantum_999 import read,file_hash
from lotto645 import load_draws_jsonl,data_hash
import argparse
import concurrent.futures as cf
import ctypes
import gzip
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import time
import numpy as np
from scipy.stats import beta

ROOT=frozen.ROOT
OUT=ROOT/"data/lotto/reports/quantum-million"
OLD=ROOT/"data/lotto/reports/quantum-99999"
TOTAL=999999
PREFIX=99999
BLOCK=500
NAMES=("validation","final","seen_extension")


def available_cores():
    if os.name!="nt":return sorted(os.sched_getaffinity(0))
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    k.GetCurrentProcess.restype=ctypes.c_void_p
    k.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
    mask,system=ctypes.c_size_t(),ctypes.c_size_t()
    if not k.GetProcessAffinityMask(k.GetCurrentProcess(),ctypes.byref(mask),ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())
    return [i for i in range(64) if mask.value & (1<<i)]


def set_core(core):
    if os.name=="nt":
        k=ctypes.WinDLL("kernel32",use_last_error=True)
        k.GetCurrentProcess.restype=ctypes.c_void_p
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        if not k.SetProcessAffinityMask(k.GetCurrentProcess(),1<<core):
            raise ctypes.WinError(ctypes.get_last_error())
    else:os.sched_setaffinity(0,{core})
    frozen.constrain_process()


def worker_init(queue):
    set_core(queue.get(timeout=30))


def compute(indices):
    frozen.DEADLINE=time.monotonic()+300
    started,cpu=time.monotonic(),time.process_time()
    rows=[]
    for i in indices:
        before=time.monotonic();frozen.guard()
        rng=np.random.default_rng(2026101700+i)
        rows.append(frozen.evaluate(rng.choice(64,1245,p=frozen.BASE)))
        time.sleep(max(.005,.25*(time.monotonic()-before)))
    return rows,{"pid":os.getpid(),"wall_seconds":time.monotonic()-started,
        "cpu_seconds":time.process_time()-cpu,"peak_memory_mib":frozen.peak_memory()/1024**2}


def starts():return range(PREFIX,TOTAL,BLOCK)


def read_new(path,protocol):
    with gzip.open(path,"rt",encoding="utf-8") as f:payload=__import__("json").load(f)
    start=payload["start"];end=payload["end"]
    assert start in starts() and end==min(start+BLOCK,TOTAL)
    assert len(payload["rows"])==end-start and payload["protocol_hash"]==canonical_hash(protocol)
    assert path.name==f"histories-{start:06d}-{end-1:06d}.json.gz"
    return payload


def all_rows(protocol):
    for i,row in previous.iter_results(read(OLD/"protocol.json")):yield i,row
    manifest=read(OUT/"archive-manifest.json")
    assert len(manifest)==len(starts())
    for entry,start in zip(manifest,starts(),strict=True):
        path=OUT/entry["file"]
        assert file_hash(path)==entry["sha256"] and entry["start"]==start
        p=read_new(path,protocol)
        for j,row in enumerate(p["rows"]):yield start+j,row


def tail(observed,values):
    n=len(values);c=int(np.count_nonzero(values>=observed))
    return {"observed":observed,"exceedances":c,"null_histories":n,"p_upper_plus_one":(c+1)/(n+1),
        "tail_probability_ci95":[0. if c==0 else float(beta.ppf(.025,c,n-c+1)),
            1. if c==n else float(beta.ppf(.975,c+1,n-c))],
        "null_interval_95":np.quantile(values,[.025,.975]).tolist()}


def main(max_new_blocks=None):
    lock=previous.lock_process()  # Dedicated worktree, shared with its verifier.
    cores=available_cores()[:2];assert len(cores)==2
    frozen.OUT=OUT
    frozen.DEADLINE=time.monotonic()+300
    old_protocol=read(OLD/"protocol.json")
    source_hash=hashlib.sha256(Path(frozen.__file__).read_text(encoding="utf-8").encode()).hexdigest()
    assert source_hash==old_protocol["core_source_sha256_lf"]
    protocol={"total_null_histories":TOTAL,"prefix_histories":PREFIX,"new_histories":900000,
        "core_source_sha256_lf":source_hash,"frozen_model_protocol":old_protocol["frozen_model_protocol"],
        "prefix_manifest_sha256":file_hash(OLD/"archive-manifest.json"),
        "prefix_actual_sha256":file_hash(OLD/"actual.json"),"seed_start":2026101700,
        "workers":2,"logical_cores":cores,"priority":"BelowNormal","blas_threads":1,
        "block_size":BLOCK,"max_pending_blocks":2,"sleep_rule":"max(5ms,25% of fit wall time) after each fit",
        "memory_guard_mib_per_process":256,"block_wall_budget_seconds":300,"launch_budget_hours":48,
        "primary_endpoint":"final mean log gain","model_changes":"none; only execution concurrency and rest interval change"}
    if (OUT/"protocol.json").exists():assert read(OUT/"protocol.json")==protocol
    else:frozen.save("protocol.json",protocol)
    base=load_draws_jsonl(ROOT/"data/lotto/draws.jsonl")
    extra=load_draws_jsonl(ROOT/"data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    assert data_hash(base)==protocol["frozen_model_protocol"]["base_hash"]
    assert data_hash(extra)==protocol["frozen_model_protocol"]["extra_hash"]
    set_core(cores[0])
    actual=read(OLD/"actual.json")
    assert frozen.evaluate(np.array([frozen.category(d.numbers_sorted) for d in base+extra]),True)==actual
    frozen.save("actual.json",actual)
    scores=np.empty((3,TOTAL));success=0;selected={};prefix_count=0
    for i,row in previous.iter_results(old_protocol):
        assert i==prefix_count;prefix_count+=1
        for k,s in enumerate(NAMES):scores[k,i]=row["splits"][s]["mean_log_gain"]
        success+=int(row["fit"]["success"])
        if i in (0,998,9998,99998):selected[i]=row
    assert prefix_count==PREFIX
    prior_manifest={e["file"]:e for e in read(OUT/"archive-manifest.json")} if (OUT/"archive-manifest.json").exists() else {}
    entries={};worker_cpu=0.;max_worker_memory=0.
    for path in sorted(OUT.glob("histories-*.json.gz")):
        if path.name in prior_manifest:assert file_hash(path)==prior_manifest[path.name]["sha256"]
        p=read_new(path,protocol)
        entries[p["start"]]={"file":path.name,"sha256":file_hash(path),"start":p["start"],"end":p["end"]}
        for j,row in enumerate(p["rows"]):
            for k,s in enumerate(NAMES):scores[k,p["start"]+j]=row["splits"][s]["mean_log_gain"]
            success+=int(row["fit"]["success"])
        worker_cpu+=p["resource"]["cpu_seconds"]
        max_worker_memory=max(max_worker_memory,p["resource"]["peak_memory_mib"])
    resumed_count=PREFIX+sum(e["end"]-e["start"] for e in entries.values())
    started=time.monotonic();ctx=mp.get_context("spawn");queue=ctx.Queue()
    for core in cores:queue.put(core)
    with cf.ProcessPoolExecutor(max_workers=2,mp_context=ctx,initializer=worker_init,initargs=(queue,)) as pool:
        preflight_jobs=[pool.submit(compute,ids) for ids in ([0,9998],[998,99998])]
        for job,ids in zip(preflight_jobs,([0,9998],[998,99998])):
            rows,resources=job.result()
            for i,row in zip(ids,rows):assert row==selected[i],f"Parallel history {i} changed"
        frozen.save("preflight.json",{"actual_exact":True,"parallel_prefix_indices_exact":sorted(selected),
            "validated_prefix_count":PREFIX,"source_and_data_hashes":"unchanged","resumed_count":resumed_count})
        missing=[i for i in starts() if i not in entries]
        if max_new_blocks is not None:missing=missing[:max_new_blocks]
        iterator=iter(missing);pending={}
        def submit_next():
            start=next(iterator,None)
            if start is not None:pending[pool.submit(compute,range(start,min(start+BLOCK,TOTAL)))]=start
        submit_next();submit_next()
        frozen.DEADLINE=started+48*3600
        while pending:
            frozen.guard()
            assert time.monotonic()-started<48*3600
            done,_=cf.wait(pending,timeout=30,return_when=cf.FIRST_COMPLETED)
            for future in done:
                start=pending.pop(future);rows,resources=future.result();end=start+len(rows)
                path=OUT/f"histories-{start:06d}-{end-1:06d}.json.gz"
                write_archive(path,{"protocol_hash":canonical_hash(protocol),"start":start,"end":end,"rows":rows,"resource":resources})
                entries[start]={"file":path.name,"sha256":file_hash(path),"start":start,"end":end}
                for j,row in enumerate(rows):
                    for k,s in enumerate(NAMES):scores[k,start+j]=row["splits"][s]["mean_log_gain"]
                    success+=int(row["fit"]["success"])
                worker_cpu+=resources["cpu_seconds"];max_worker_memory=max(max_worker_memory,resources["peak_memory_mib"])
                frozen.save("archive-manifest.json",[entries[i] for i in sorted(entries)])
                frozen.save("progress.json",{"state":"running","completed":PREFIX+sum(e["end"]-e["start"] for e in entries.values()),
                    "target":TOTAL,"updated_unix":time.time(),"coordinator_pid":os.getpid(),"elapsed_seconds_this_launch":time.monotonic()-started,
                    "resumed_count":resumed_count,"worker_cpu_seconds_all_new_blocks":worker_cpu,
                    "max_worker_memory_mib":max_worker_memory,"coordinator_peak_memory_mib":frozen.peak_memory()/1024**2})
                print(f"saved {len(entries)}/{len(starts())} blocks",flush=True)
                submit_next()
    if len(entries)<len(starts()):print("Smoke budget complete; resume using the same command",flush=True);return
    summary={"complete":True,"total_null_histories":TOTAL,"reused_prefix_histories":PREFIX,"new_histories":900000,
        "fit_success_count":success,"resumed_count":resumed_count,"wall_seconds_this_launch":time.monotonic()-started,
        "worker_cpu_seconds_all_new_blocks":worker_cpu,"max_worker_memory_mib":max_worker_memory,
        "coordinator_peak_memory_mib":frozen.peak_memory()/1024**2,
        "comparisons":{s:tail(actual["splits"][s]["mean_log_gain"],scores[k]) for k,s in enumerate(NAMES)}}
    frozen.save("summary.json",summary)
    frozen.save("progress.json",{"state":"computed_pending_verification","completed":TOTAL,"target":TOTAL,"updated_unix":time.time()})
    print(summary,flush=True);lock.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--max-new-blocks",type=int)
    main(parser.parse_args().max_new_blocks)
