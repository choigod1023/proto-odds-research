"""Dense-gate verification of saved six-qubit predictions and null statistics."""
import json
import math
from pathlib import Path
import sys
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))
from lotto_small_quantum import BASE, OUT, constrain_process, save
from lotto645 import load_draws_jsonl

constrain_process()
actual = json.loads((OUT / "actual.json").read_text())
theta = actual["fit"]["theta"]
state = np.eye(64)[:,0]
for layer in range(2):
    for q in range(6):
        a = theta[layer*6+q]/2
        rotation = np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])
        gate = np.ones((1,1))
        for bit in range(5,-1,-1):
            gate = np.kron(gate,rotation if bit==q else np.eye(2))
        state = gate @ state
    for q in range(6):
        gate = np.zeros((64,64))
        for i in range(64):
            j = i ^ (1 << ((q+1)%6)) if i & (1 << q) else i
            gate[j,i]=1
        state=gate @ state
born = state**2
prob = .05*BASE + .95*(BASE*born)/sum(BASE*born)
np.testing.assert_allclose(prob,actual["category_probabilities"],atol=1e-14)
draws = load_draws_jsonl(ROOT / "data/lotto/draws.jsonl") + load_draws_jsonl(
    ROOT / "data/lotto/reports/null-expanded/new-rounds/draws.jsonl")
errors = []
for draw,saved in zip(draws[740:],actual["per_round"],strict=True):
    code = 8*len([n for n in draw.numbers_sorted if n<23])+len([n for n in draw.numbers_sorted if n%2])
    assert saved["draw_no"]==draw.draw_no and saved["category"]==code
    errors.append(abs(math.log(prob[code]/BASE[code])-saved["log_gain"]))
assert max(errors)<1e-12
nulls=json.loads((OUT/"nulls.json").read_text())
summary=json.loads((OUT/"summary.json").read_text())
assert len(nulls)==99
for name,result in summary["comparison"].items():
    observed=actual["splits"][name]["mean_log_gain"]
    p=(1+sum(row["splits"][name]["mean_log_gain"]>=observed for row in nulls))/100
    assert p==result["p_upper_plus_one"]
result={"status":"passed","dense_reference_rounds":len(errors),"maximum_log_score_error":max(errors),
        "null_histories":len(nulls),"unit_norm_error":abs(float(state@state)-1)}
save("verification.json",result)
print(result)
