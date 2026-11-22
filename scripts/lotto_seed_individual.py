"""Descriptive per-seed comparison against the primary experiment's controls.

This does not select a new portfolio or modify the primary protocol/results.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto645 import load_draws_jsonl
from lotto_seed_expanded import evaluate, uniform_controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("data/lotto/reports/seed-triple"))
    parser.add_argument("--data", type=Path, default=Path("data/lotto/draws.jsonl"))
    args = parser.parse_args()
    primary = json.loads((args.report / "experiment.json").read_text())
    protocol = primary["protocol"]
    selected = np.array([int(row["seed"]) for row in primary["training_hits"]], dtype=np.uint64)
    if not len(selected):
        raise ValueError("no survivors to compare")
    controls = uniform_controls(selected, protocol["baseline_repeats"], protocol["control_seed"])
    assert hashlib.sha256(controls.astype("<u8").tobytes()).hexdigest() == primary["control_sha256"]
    draws = load_draws_jsonl(args.data)
    output = {"interpretation": "descriptive per-seed comparison, no reselection; reused historical evaluation data",
              "control_sha256": primary["control_sha256"], "individual_control_seeds": controls.size}
    for key in ("validation", "final"):
        lo, hi = primary[key]["draw_range"]
        rows = [d for d in draws if lo <= d.draw_no <= hi]
        reference = evaluate(selected[:1], controls.reshape(-1, 1), rows)
        assert reference["selected_histogram"] == primary[key]["per_seed"][0]["histogram"]
        totals = np.array(reference["baseline_histograms"]) @ np.arange(7)
        means = totals / len(rows)
        output[key] = {"baseline_mean_matches": float(means.mean()),
                       "baseline_reference_interval_95": np.quantile(means, [.025, .975]).tolist(),
                       "per_seed": [{**row, "baseline_fraction_at_least_selected":
                                     float(np.mean(totals >= np.array(row["histogram"]) @ np.arange(7)))}
                                    for row in primary[key]["per_seed"]]}
    (args.report / "individual-comparison.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
