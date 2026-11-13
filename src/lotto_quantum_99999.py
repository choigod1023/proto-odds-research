"""Streaming, locked background extension of the unchanged six-qubit model."""
import lotto_small_quantum as frozen
from lotto_quantum_999 import read,file_hash,tail_summary
from lotto_quantum_9999 import canonical_hash,write_archive
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np
from lotto645 import load_draws_jsonl,data_hash

ROOT=frozen.ROOT
OUT=ROOT/"data/lotto/reports/quantum-99999"
PREFIX=ROOT/"data/lotto/reports/quantum-9999"
TOTAL=99999
SPLITS=("validation","final","seen_extension")


def lock_process():
    """OS-held lock is released automatically when the process exits."""
    local=ROOT/".local-build";local.mkdir(exist_ok=True)
    handle=(local/"quantum-99999.lock").open("a+b")
    handle.seek(0)
    if os.name=="nt":
        import msvcrt
        if not handle.read(1):
            handle.write(b"0");handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return handle


def load_block(path,expected_start,protocol_hash):
    with gzip.open(path,"rt",encoding="utf-8") as f:
        payload=json.load(f)
    assert payload["start"]==expected_start
    assert payload["protocol_hash"]==protocol_hash
    assert payload["end"]==expected_start+len(payload["rows"])
    assert 0<len(payload["rows"])<=100
    return payload


def iter_results(protocol):
    """Never retain all fit objects in RAM; validate hashes and continuity."""
    pos=0
    old_hash=canonical_hash(read(PREFIX/"protocol.json"))
    for entry in read(PREFIX/"archive-manifest.json"):
        path=PREFIX/entry["file"]
        assert file_hash(path)==entry["sha256"]
        block=load_block(path,pos,old_hash)
        assert entry["start"]==block["start"] and entry["end"]==block["end"]
        for row in block["rows"]:
            yield pos,row
            pos+=1
    assert pos==9999
    ph=canonical_hash(protocol)
    old_manifest={e["file"]:e for e in read(OUT/"archive-manifest.json")} if (OUT/"archive-manifest.json").exists() else {}
    for path in sorted(OUT.glob("histories-*.json.gz")):
        if path.name in old_manifest:
            assert file_hash(path)==old_manifest[path.name]["sha256"]
        block=load_block(path,pos,ph)
        assert path.name==f'histories-{pos:06d}-{block["end"]-1:06d}.json.gz'
        assert block["end"]<=TOTAL
        for row in block["rows"]:
            yield pos,row
            pos+=1


def protocol_for(limits):
    previous=read(PREFIX/"protocol.json")
    core_hash=hashlib.sha256(Path(frozen.__file__).read_text(encoding="utf-8").encode()).hexdigest()
    assert core_hash==previous["core_source_sha256_lf"]
    return {"total_null_histories":TOTAL,"prefix_histories":9999,
        "frozen_model_protocol":previous["frozen_model_protocol"],"core_source_sha256_lf":core_hash,
        "prefix_manifest_sha256":file_hash(PREFIX/"archive-manifest.json"),
        "prefix_actual_sha256":file_hash(PREFIX/"actual.json"),
        "seed_start":2026101700,"limits":limits,"block_size":100,
        "sleep_rule":"sleep at least the fit wall time and 25ms per fit; one second per block",
        "memory_guard_mib":256,"per_block_wall_budget_seconds":120,"per_launch_max_hours":12,
        "primary_endpoint":"final mean log gain","model_changes":"none",
        "verification":"stream all stored circuits through independent dense matrix gates after completion"}


def main(max_new_blocks=None,preflight_only=False):
    lock=lock_process()
    limits=frozen.constrain_process();frozen.OUT=OUT
    started,cpu=time.monotonic(),time.process_time()
    frozen.DEADLINE=started+120
    protocol=protocol_for(limits)
    if (OUT/"protocol.json").exists():
        assert read(OUT/"protocol.json")==protocol
    else:
        frozen.save("protocol.json",protocol)
    base=load_draws_jsonl(ROOT/"data/lotto/draws.jsonl")
    extra=load_draws_jsonl(ROOT/"data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    core=protocol["frozen_model_protocol"]
    assert data_hash(base)==core["base_hash"] and data_hash(extra)==core["extra_hash"]
    actual=read(PREFIX/"actual.json")
    assert frozen.evaluate(np.array([frozen.category(d.numbers_sorted) for d in base+extra]),True)==actual
    frozen.save("actual.json",actual)
    scores={s:[] for s in SPLITS};success=0;completed=0;preflight=[]
    for i,row in iter_results(protocol):
        completed=i+1;success+=int(row["fit"]["success"])
        for s in SPLITS:
            scores[s].append(row["splits"][s]["mean_log_gain"])
        if i in (0,998,9998):
            r=np.random.default_rng(2026101700+i)
            assert frozen.evaluate(r.choice(64,1245,p=frozen.BASE))==row
            preflight.append(i)
    frozen.save("preflight.json",{"actual_exact":True,"replayed_prefix_indices":preflight,
        "validated_prefix_histories":9999,"completed_at_start":completed,
        "source_hash_matches":True,"peak_memory_mib":frozen.peak_memory()/1024**2})
    if preflight_only:
        print("Preflight passed",flush=True);return
    resumed_at=completed
    manifest=[]
    for path in sorted(OUT.glob("histories-*.json.gz")):
        with gzip.open(path,"rt",encoding="utf-8") as f:
            b=json.load(f)
        manifest.append({"file":path.name,"sha256":file_hash(path),"start":b["start"],"end":b["end"]})
    frozen.save("archive-manifest.json",manifest)
    frozen.save("worker.json",{"pid":os.getpid(),"started_unix":time.time(),"resumed_at":resumed_at})
    blocks=0
    print(f"Resuming {completed}/{TOTAL}",flush=True)
    while completed<TOTAL:
        assert time.monotonic()-started<12*3600,"Launch budget exceeded; resume from checkpoints"
        frozen.DEADLINE=time.monotonic()+120
        start=completed;stop=min(TOTAL,start+100);batch=[]
        for i in range(start,stop):
            before=time.monotonic();frozen.guard()
            rng=np.random.default_rng(2026101700+i)
            row=frozen.evaluate(rng.choice(64,1245,p=frozen.BASE))
            batch.append(row)
            time.sleep(max(.025,time.monotonic()-before))
        path=OUT/f"histories-{start:06d}-{stop-1:06d}.json.gz"
        write_archive(path,{"protocol_hash":canonical_hash(protocol),"start":start,"end":stop,"rows":batch})
        manifest.append({"file":path.name,"sha256":file_hash(path),"start":start,"end":stop})
        frozen.save("archive-manifest.json",manifest)
        for row in batch:
            success+=int(row["fit"]["success"])
            for s in SPLITS:
                scores[s].append(row["splits"][s]["mean_log_gain"])
        completed=stop;blocks+=1
        progress={"state":"running","completed":completed,"target":TOTAL,"resumed_at":resumed_at,
            "pid":os.getpid(),"updated_unix":time.time(),"elapsed_seconds_this_launch":time.monotonic()-started,
            "cpu_seconds_this_launch":time.process_time()-cpu,"peak_memory_mib":frozen.peak_memory()/1024**2}
        frozen.save("progress.json",progress);print(json.dumps(progress),flush=True)
        if max_new_blocks is not None and blocks>=max_new_blocks:
            print("Requested block budget complete; rerun to resume",flush=True);return
        time.sleep(1)
    summary={"complete":True,"total_null_histories":TOTAL,"reused_prefix_histories":9999,
        "new_histories":90000,"fit_success_count":success,"resumed_at":resumed_at,
        "wall_seconds_this_launch":time.monotonic()-started,"cpu_seconds_this_launch":time.process_time()-cpu,
        "peak_memory_mib":frozen.peak_memory()/1024**2,
        "comparisons":{s:tail_summary(actual["splits"][s]["mean_log_gain"],scores[s]) for s in SPLITS}}
    frozen.save("summary.json",summary)
    frozen.save("progress.json",{"state":"computed_pending_verification","completed":TOTAL,"target":TOTAL,
        "updated_unix":time.time(),"pid":os.getpid()})
    print(json.dumps(summary),flush=True)
    lock.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--max-new-blocks",type=int)
    parser.add_argument("--preflight-only",action="store_true")
    args=parser.parse_args()
    main(args.max_new_blocks,args.preflight_only)
