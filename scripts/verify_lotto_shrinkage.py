"""Independently check saved actual scores against scalar combination formula."""
import json
import math
from pathlib import Path
import sys
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lotto645 import load_draws_jsonl, joint_log_probability
from lotto_shrinkage import CONFIGS, OUT, durable

actual = json.loads((OUT / "actual.json").read_text(encoding="utf-8"))
draws = load_draws_jsonl(ROOT / "data/lotto/draws.jsonl") + load_draws_jsonl(
    ROOT / "data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
half_life, strength = CONFIGS[actual["selected"]]
counts = np.zeros(45)
mass = 0.0
errors = []
for t, draw in enumerate(draws):
    if strength is None:
        value = 0.0
    else:
        p = (counts + strength * 6 / 45) / (mass + strength)
        value = joint_log_probability(draw.numbers_sorted, np.log(p / (1 - p))) + math.log(math.comb(45, 6))
    if t >= 740:
        errors.append(abs(value - actual["per_round"][t - 740]["log_gain"]))
    decay = 1.0 if half_life is None else 2 ** (-1 / half_life)
    counts *= decay
    counts[np.array(draw.numbers_sorted) - 1] += 1
    mass = decay * mass + 1
assert max(errors) < 1e-12
result = {"status": "passed", "independent_scalar_rounds": len(errors), "max_abs_error": max(errors)}
durable(OUT / "verification.json", result)
print(result)
