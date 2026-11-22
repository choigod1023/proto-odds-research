"""Extend the frozen six-qubit experiment to 999 null histories, serially."""
import lotto_small_quantum as frozen  # Sets numerical-library threads before imports.
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.stats import beta
from lotto645 import load_draws_jsonl, data_hash

OUT = frozen.ROOT / "data/lotto/reports/quantum-999"
OLD = frozen.ROOT / "data/lotto/reports/small-quantum"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tail_summary(observed, values):
    n = len(values)
    count = sum(v >= observed for v in values)
    return {"observed": observed, "exceedances": count, "null_histories": n,
        "p_upper_plus_one": (count+1)/(n+1),
        "tail_probability_ci95": [0.0 if count==0 else float(beta.ppf(.025,count,n-count+1)),
                                   1.0 if count==n else float(beta.ppf(.975,count+1,n-count))],
        "null_interval_95": np.quantile(values,[.025,.975]).tolist()}


def main():
    limits = frozen.constrain_process()
    start, cpu = time.monotonic(), time.process_time()
    frozen.DEADLINE = start + 120
    protocol = read(OLD / "protocol.json")
    source_hash = hashlib.sha256(Path(frozen.__file__).read_text(encoding="utf-8").encode()).hexdigest()
    assert source_hash == protocol["source_sha256_lf"]
    base = load_draws_jsonl(frozen.ROOT / "data/lotto/draws.jsonl")
    extra = load_draws_jsonl(frozen.ROOT / "data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    assert data_hash(base)==protocol["base_hash"] and data_hash(extra)==protocol["extra_hash"]
    actual = read(OLD/"actual.json")
    previous = read(OLD/"nulls.json")
    assert len(previous)==99
    frozen.OUT = OUT
    frozen.save("protocol.json", {"frozen_protocol": protocol, "null_histories": 999,
        "source_sha256_lf": source_hash, "previous_null_file_sha256": file_hash(OLD/"nulls.json"),
        "previous_actual_file_sha256": file_hash(OLD/"actual.json"),
        "limits": limits, "wall_seconds_cap":120,"checkpoint_every":50,
        "sleep_seconds_per_history":.025,"primary_endpoint":"final mean log gain",
        "changes":"Only null history count increased; all original 99 refitted for equality; no new actual draws"})
    replay = frozen.evaluate(np.array([frozen.category(d.numbers_sorted) for d in base+extra]),True)
    assert replay==actual, "Actual replay changed"
    frozen.save("actual.json", replay)
    nulls = []
    for i in range(999):
        frozen.guard()
        rng = np.random.default_rng(2026101700+i)
        row = frozen.evaluate(rng.choice(64,1245,p=frozen.BASE))
        if i<99:
            assert row==previous[i], f"Frozen history {i} changed"
        nulls.append(row)
        if (i+1)%50==0:
            frozen.save("nulls.json",nulls)
            print(f"null {i+1}/999; elapsed {time.monotonic()-start:.1f}s",flush=True)
        time.sleep(.025)
    frozen.save("nulls.json",nulls)
    summary={"comparisons":{},"actual_replay_exact":True,"prefix_replay_exact":99,
        "fit_success_count":sum(r["fit"]["success"] for r in nulls)}
    for name, scores in actual["splits"].items():
        summary["comparisons"][name]=tail_summary(scores["mean_log_gain"],
            [r["splits"][name]["mean_log_gain"] for r in nulls])
    summary.update(wall_seconds=time.monotonic()-start,cpu_seconds=time.process_time()-cpu,
                   peak_process_memory_mib=frozen.peak_memory()/1024**2)
    frozen.save("summary.json",summary)
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__":
    try:
        main()
    except (TimeoutError,MemoryError) as exc:
        frozen.save("stopped.json",{"complete":False,"reason":str(exc)})
        raise
