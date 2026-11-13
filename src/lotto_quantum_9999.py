"""Frozen six-qubit null extension, resumable and deliberately CPU-throttled."""
import lotto_small_quantum as frozen
from lotto_quantum_999 import read, file_hash, tail_summary
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np
from lotto645 import data_hash, load_draws_jsonl

OUT = frozen.ROOT / "data/lotto/reports/quantum-9999"
OLD = frozen.ROOT / "data/lotto/reports/quantum-999"
TOTAL = 9999
BLOCK = 100


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",", ":")).encode()).hexdigest()


def write_archive(path, payload):
    """Flush to disk, reopen and compare, then publish the completed block."""
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    with temp.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw,mode="wb",mtime=0) as zipped:
            zipped.write(json.dumps(payload,separators=(",",":"),allow_nan=False).encode())
        raw.flush(); os.fsync(raw.fileno())
    with gzip.open(temp,"rt",encoding="utf-8") as f:
        assert json.load(f)==payload
    temp.replace(path)


def audit_archives(directory, protocol_hash):
    rows=[]; manifest=[]
    for path in sorted(directory.glob("histories-*.json.gz")):
        with gzip.open(path,"rt",encoding="utf-8") as f:
            block=json.load(f)
        assert block["protocol_hash"]==protocol_hash
        assert block["start"]==len(rows)
        assert block["end"]==block["start"]+len(block["rows"])
        assert 0<len(block["rows"])<=BLOCK
        assert path.name==f'histories-{block["start"]:05d}-{block["end"]-1:05d}.json.gz'
        rows.extend(block["rows"])
        manifest.append({"file":path.name,"sha256":file_hash(path),"start":block["start"],"end":block["end"]})
    assert len(rows)<=TOTAL
    return rows,manifest


def main():
    limits=frozen.constrain_process()
    started,cpu=time.monotonic(),time.process_time()
    frozen.OUT=OUT
    frozen.DEADLINE=started+1200
    core_protocol=read(frozen.ROOT/"data/lotto/reports/small-quantum/protocol.json")
    core_hash=hashlib.sha256(Path(frozen.__file__).read_text(encoding="utf-8").encode()).hexdigest()
    assert core_hash==core_protocol["source_sha256_lf"]
    base=load_draws_jsonl(frozen.ROOT/"data/lotto/draws.jsonl")
    extra=load_draws_jsonl(frozen.ROOT/"data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    assert data_hash(base)==core_protocol["base_hash"] and data_hash(extra)==core_protocol["extra_hash"]
    previous=read(OLD/"nulls.json"); actual=read(OLD/"actual.json")
    assert len(previous)==999
    protocol={"frozen_model_protocol":core_protocol,"total_null_histories":TOTAL,
        "core_source_sha256_lf":core_hash,"previous_null_sha256":file_hash(OLD/"nulls.json"),
        "previous_actual_sha256":file_hash(OLD/"actual.json"),"seed_start":2026101700,
        "primary_endpoint":"final mean log gain","secondary_endpoints":["validation","seen_extension"],
        "limits":limits,"wall_seconds_cap_per_launch":1200,"block_size":BLOCK,
        "sleep_rule":"After each fit, sleep max(0.025 seconds, fit elapsed wall time); extra 1 second after each block",
        "model_and_data_changes":"None; original actual and all original 999 nulls must replay exactly"}
    if (OUT/"protocol.json").exists():
        assert read(OUT/"protocol.json")==protocol,"Protocol differs; refusing to reuse checkpoints"
    else:
        frozen.save("protocol.json",protocol)
    ph=canonical_hash(protocol)
    rows,manifest=audit_archives(OUT,ph)
    assert rows[:min(999,len(rows))]==previous[:min(999,len(rows))]
    resumed_at=len(rows)
    replay=frozen.evaluate(np.array([frozen.category(d.numbers_sorted) for d in base+extra]),True)
    assert replay==actual
    frozen.save("actual.json",replay)
    frozen.save("archive-manifest.json",manifest)
    print(f"Starting at {resumed_at}/{TOTAL}; original actual replay exact",flush=True)
    while len(rows)<TOTAL:
        start=len(rows); stop=min(TOTAL,start+BLOCK); batch=[]
        for i in range(start,stop):
            fit_start=time.monotonic()
            frozen.guard()
            rng=np.random.default_rng(2026101700+i)
            row=frozen.evaluate(rng.choice(64,1245,p=frozen.BASE))
            if i<999:
                assert row==previous[i],f"Frozen null {i} changed"
            batch.append(row)
            time.sleep(max(.025,time.monotonic()-fit_start))
        path=OUT/f"histories-{start:05d}-{stop-1:05d}.json.gz"
        write_archive(path,{"protocol_hash":ph,"start":start,"end":stop,"rows":batch})
        rows.extend(batch)
        manifest.append({"file":path.name,"sha256":file_hash(path),"start":start,"end":stop})
        frozen.save("archive-manifest.json",manifest)
        progress={"completed":len(rows),"target":TOTAL,"elapsed_seconds":time.monotonic()-started,
            "cpu_seconds":time.process_time()-cpu,"peak_memory_mib":frozen.peak_memory()/1024**2}
        frozen.save("progress.json",progress)
        print(json.dumps(progress),flush=True)
        time.sleep(1)
    summary={"comparisons":{},"actual_replay_exact":True,"prefix_replay_exact":999,
        "fit_success_count":sum(row["fit"]["success"] for row in rows),"archive_blocks":len(manifest),
        "resumed_at":resumed_at,"wall_seconds_this_launch":time.monotonic()-started,
        "cpu_seconds_this_launch":time.process_time()-cpu,"peak_memory_mib":frozen.peak_memory()/1024**2}
    for name,score in actual["splits"].items():
        summary["comparisons"][name]=tail_summary(score["mean_log_gain"],[r["splits"][name]["mean_log_gain"] for r in rows])
    frozen.save("summary.json",summary)
    print(json.dumps(summary),flush=True)


if __name__=="__main__":
    try:
        main()
    except (TimeoutError,MemoryError) as exc:
        frozen.save("stopped.json",{"complete":False,"reason":str(exc),"resume":"Rerun the same command; validated full blocks are retained"})
        raise
