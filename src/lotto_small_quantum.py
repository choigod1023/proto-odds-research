"""Bounded six-qubit statevector experiment, not quantum hardware execution."""
import os
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"
import ctypes
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
from scipy.optimize import minimize
from lotto645 import load_draws_jsonl, data_hash

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/lotto/reports/small-quantum"
DEADLINE = float("inf")
PROCESS = None


def constrain_process():
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel.GetCurrentProcess()
        mask, system = ctypes.c_size_t(), ctypes.c_size_t()
        kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        if not kernel.GetProcessAffinityMask(handle, ctypes.byref(mask), ctypes.byref(system)):
            raise ctypes.WinError(ctypes.get_last_error())
        core = mask.value & -mask.value
        if not kernel.SetProcessAffinityMask(handle, core) or not kernel.SetPriorityClass(handle, 0x4000):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"affinity_mask": core, "priority": "BELOW_NORMAL_PRIORITY_CLASS"}
    available = os.sched_getaffinity(0)
    os.sched_setaffinity(0, {min(available)})
    os.nice(5)
    return {"cpu": min(available), "nice_increment": 5}


def peak_memory():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("peak", "working", "pp", "p", "np", "n", "page", "peakpage")]
    k = ctypes.WinDLL("kernel32"); k.GetCurrentProcess.restype = ctypes.c_void_p
    p = ctypes.WinDLL("psapi")
    p.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    c = Counters(); c.cb = ctypes.sizeof(c)
    if not p.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise OSError("Cannot measure process memory")
    return int(c.peak)


def guard():
    if time.monotonic() > DEADLINE:
        raise TimeoutError("120 second experiment budget exhausted")
    if os.name == "nt" and peak_memory() > 256 * 1024 ** 2:
        raise MemoryError("256 MiB process memory budget exhausted")


def save(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.flush(); os.fsync(f.fileno())
    temp.replace(path)


def category_counts():
    counts = np.zeros(64, dtype=np.int64)
    # low odd/even = 11/11; high odd/even = 12/11.
    for a in range(7):
        for b in range(7 - a):
            for c in range(7 - a - b):
                d = 6 - a - b - c
                counts[(a + b) * 8 + a + c] += math.comb(11, a) * math.comb(11, b) * math.comb(12, c) * math.comb(11, d)
    assert counts.sum() == math.comb(45, 6)
    return counts


COUNTS = category_counts()
BASE = COUNTS / COUNTS.sum()
INDICES = np.arange(64)
PAIRS = [(INDICES[(INDICES & (1 << q)) == 0], INDICES[(INDICES & (1 << q)) == 0] | (1 << q)) for q in range(6)]
PERMS = [INDICES ^ (((INDICES >> q) & 1) << ((q + 1) % 6)) for q in range(6)]


def statevector(theta):
    state = np.zeros(64); state[0] = 1
    for layer in range(2):
        for q, (lo, hi) in enumerate(PAIRS):
            c, s = np.cos(theta[layer * 6 + q] / 2), np.sin(theta[layer * 6 + q] / 2)
            a, b = state[lo].copy(), state[hi].copy()
            state[lo], state[hi] = c * a - s * b, s * a + c * b
        for perm in PERMS:
            state = state[perm]
    return state


def probabilities(theta):
    born = statevector(theta) ** 2
    weighted = BASE * born
    # Base-measure correction and 5% uniform-combination mixture.
    return .05 * BASE + .95 * weighted / weighted.sum()


INITIAL = np.r_[np.full(6, np.pi / 2), np.zeros(6)]


def fit(train, prior=740):
    freq = np.bincount(train, minlength=64)
    target = (freq + prior * BASE) / (len(train) + prior)
    def objective(theta):
        guard()
        p = probabilities(theta)
        return -float(np.dot(target[BASE > 0], np.log(p[BASE > 0])))
    fitted = minimize(objective, INITIAL.copy(), method="L-BFGS-B",
                      options={"maxiter": 40, "maxfun": 800, "ftol": 1e-9})
    return probabilities(fitted.x), {"theta": fitted.x.tolist(), "success": bool(fitted.success),
        "message": str(fitted.message), "iterations": int(fitted.nit), "evaluations": int(fitted.nfev)}


def category(numbers):
    return sum(n <= 22 for n in numbers) * 8 + sum(n % 2 for n in numbers)


def evaluate(labels, details=False):
    predicted, info = fit(labels[:740])
    classical = (np.bincount(labels[:740], minlength=64) + 740 * BASE) / 1480
    gains = np.log(predicted[labels] / BASE[labels])
    classical_gains = np.log(classical[labels] / BASE[labels])
    result = {"fit": info, "splits": {}}
    for name, lo, hi in (("validation", 740, 990), ("final", 990, 1238), ("seen_extension", 1238, 1245)):
        result["splits"][name] = {"n": hi-lo, "mean_log_gain": float(gains[lo:hi].mean()),
            "classical_mean_log_gain": float(classical_gains[lo:hi].mean())}
    if details:
        result["category_probabilities"] = predicted.tolist()
        result["per_round"] = [{"draw_no": i+1, "category": int(labels[i]), "log_gain": float(gains[i])} for i in range(740, 1245)]
    return result


def main():
    global DEADLINE
    limits = constrain_process()
    started = time.monotonic(); cpu = time.process_time(); DEADLINE = started + 120
    draws = load_draws_jsonl(ROOT / "data/lotto/draws.jsonl")
    extra = load_draws_jsonl(ROOT / "data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
    assert [d.draw_no for d in draws+extra] == list(range(1,1246))
    save("protocol.json", {"qubits": 6, "layers": 2, "parameters": 12, "gates": "RY, nearest-neighbor ring CNOT",
        "encoding": "8 * count(numbers <= 22) + count(odd numbers)", "uniform_combination_category_counts": COUNTS.tolist(),
        "train": [1,740], "primary": [991,1238], "secondary": [[741,990],[1239,1245]],
        "model": "base-weighted Born probabilities, 5% uniform-combination mixture, within-category uniform",
        "prior_mass": 740, "optimizer": {"method": "L-BFGS-B", "maxiter": 40, "maxfun": 800, "ftol": 1e-9},
        "initial_angles": INITIAL.tolist(), "null_histories": 99, "null_seed_start": 2026101700,
        "null_method": "draw exact category distribution induced by uniform six-subsets; sufficient statistics for this fixed model",
        "limits": limits, "wall_seconds_cap": 120, "memory_mib_guard": 256, "parallel_workers": 1,
        "base_hash": data_hash(draws), "extra_hash": data_hash(extra),
        "source_sha256_lf": hashlib.sha256(Path(__file__).read_text(encoding="utf-8").encode()).hexdigest(),
        "limitations": "Retrospective reused data; no real quantum hardware or quantum advantage claim; primary endpoint is log probability, not jackpot count"})
    # Teacher circuit is fixed before looking at Lotto; training and evaluation independent.
    rng = np.random.default_rng(6016)
    teacher = probabilities(np.linspace(.2, 2.8, 12))
    synthetic_train = rng.choice(64, 740, p=teacher)
    synthetic_test = rng.choice(64, 5000, p=teacher)
    learned, fit_info = fit(synthetic_train)
    save("sanity.json", {"fit": fit_info, "test_size": 5000,
        "mean_log_gain": float(np.log(learned[synthetic_test] / BASE[synthetic_test]).mean()),
        "teacher_mean_log_gain": float(np.log(teacher[synthetic_test] / BASE[synthetic_test]).mean())})
    labels = np.array([category(d.numbers_sorted) for d in draws+extra])
    actual = evaluate(labels, True); save("actual.json", actual)
    nulls = []
    for i in range(99):
        guard()
        r = np.random.default_rng(2026101700+i)
        nulls.append(evaluate(r.choice(64, 1245, p=BASE)))
        if (i+1) % 10 == 0:
            save("nulls.json", nulls); print(f"null {i+1}/99", flush=True)
        time.sleep(.025)
    save("nulls.json", nulls)
    comparison = {}
    for name, score in actual["splits"].items():
        observed = score["mean_log_gain"]
        values = [row["splits"][name]["mean_log_gain"] for row in nulls]
        comparison[name] = {**score, "geometric_probability_ratio": math.exp(observed),
            "p_upper_plus_one": (1+sum(v>=observed for v in values))/100,
            "null_interval_95": np.quantile(values,[.025,.975]).tolist()}
    summary = {"comparison": comparison, "null_fit_success_count": sum(r["fit"]["success"] for r in nulls),
        "actual_fit_success": actual["fit"]["success"], "wall_seconds": time.monotonic()-started,
        "cpu_seconds": time.process_time()-cpu, "peak_process_memory_mib": peak_memory()/1024**2,
        "statevector_bytes": statevector(INITIAL).nbytes}
    save("summary.json", summary); print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    try:
        main()
    except (TimeoutError, MemoryError) as exc:
        save("stopped.json", {"reason": str(exc), "complete": False})
        raise
