"""Independent dense circuit replay for all 999 fitted null histories."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import lotto_small_quantum as frozen
from lotto_quantum_999 import OUT, OLD, read, tail_summary
import math
import time
import numpy as np

frozen.constrain_process()
started=time.monotonic()
gates=[]
for q in range(6):
    gate=np.zeros((64,64))
    for i in range(64):
        j=i ^ (1 << ((q+1)%6)) if i & (1 << q) else i
        gate[j,i]=1
    gates.append(gate)


def dense_probabilities(theta):
    state=np.eye(64)[:,0]
    for layer in range(2):
        for q in range(6):
            a=theta[layer*6+q]/2
            rotation=np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])
            gate=np.ones((1,1))
            for bit in range(5,-1,-1):
                gate=np.kron(gate,rotation if bit==q else np.eye(2))
            state=gate@state
        for gate in gates:
            state=gate@state
    assert abs(state@state-1)<1e-12
    weighted=frozen.BASE*state**2
    return .05*frozen.BASE+.95*weighted/weighted.sum()


rows=read(OUT/"nulls.json")
assert len(rows)==999 and rows[:99]==read(OLD/"nulls.json")
assert read(OUT/"actual.json")==read(OLD/"actual.json")
max_error=0.0
for i,row in enumerate(rows):
    p=dense_probabilities(row["fit"]["theta"])
    rng=np.random.default_rng(2026101700+i)
    labels=rng.choice(64,1245,p=frozen.BASE)
    gains=np.log(p[labels]/frozen.BASE[labels])
    for name,lo,hi in (("validation",740,990),("final",990,1238),("seen_extension",1238,1245)):
        max_error=max(max_error,abs(float(gains[lo:hi].mean())-row["splits"][name]["mean_log_gain"]))
    if (i+1)%100==0:
        time.sleep(.05)
assert max_error<1e-12
actual=read(OUT/"actual.json")
summary=read(OUT/"summary.json")
for name,r in summary["comparisons"].items():
    expected=tail_summary(actual["splits"][name]["mean_log_gain"],[row["splits"][name]["mean_log_gain"] for row in rows])
    assert expected==r
frozen.OUT=OUT
result={"status":"passed","independent_dense_circuits":999,"evaluation_round_scores":999*505,
        "max_mean_log_gain_error":max_error,"unchanged_prefix_histories":99,"actual_unchanged":True,
        "wall_seconds":time.monotonic()-started,"peak_process_memory_mib":frozen.peak_memory()/1024**2}
frozen.save("verification.json",result)
print(result)
