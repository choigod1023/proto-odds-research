"""Independent dense gates and regenerated observations for every null circuit."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import lotto_small_quantum as frozen
from lotto_quantum_9999 import OUT,OLD,read,audit_archives,canonical_hash
import math
import time
import numpy as np
from scipy.stats import beta

frozen.constrain_process()
started=time.monotonic();cpu=time.process_time()
frozen.DEADLINE=started+300
protocol=read(OUT/"protocol.json")
rows,manifest=audit_archives(OUT,canonical_hash(protocol))
assert len(rows)==9999
assert manifest==read(OUT/"archive-manifest.json")
assert rows[:999]==read(OLD/"nulls.json")
actual=read(OUT/"actual.json")
assert actual==read(OLD/"actual.json")
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
            angle=theta[layer*6+q]/2
            rotation=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
            gate=np.ones((1,1))
            for bit in range(5,-1,-1):
                gate=np.kron(gate,rotation if bit==q else np.eye(2))
            state=gate@state
        for gate in gates:
            state=gate@state
    assert abs(state@state-1)<1e-12
    weighted=frozen.BASE*state**2
    return .05*frozen.BASE+.95*weighted/weighted.sum()


max_error=0.0
for i,row in enumerate(rows):
    before=time.monotonic()
    frozen.guard()
    p=dense_probabilities(row["fit"]["theta"])
    rng=np.random.default_rng(2026101700+i)
    labels=rng.choice(64,1245,p=frozen.BASE)
    gains=np.log(p[labels]/frozen.BASE[labels])
    for name,lo,hi in (("validation",740,990),("final",990,1238),("seen_extension",1238,1245)):
        max_error=max(max_error,abs(float(gains[lo:hi].mean())-row["splits"][name]["mean_log_gain"]))
    time.sleep(max(.002,time.monotonic()-before))
    if (i+1)%1000==0:
        print(f"dense verification {i+1}/9999",flush=True)
assert max_error<1e-12
np.testing.assert_allclose(dense_probabilities(actual["fit"]["theta"]),actual["category_probabilities"],atol=1e-14)
summary=read(OUT/"summary.json")
for name,result in summary["comparisons"].items():
    observed=actual["splits"][name]["mean_log_gain"]
    count=sum(row["splits"][name]["mean_log_gain"]>=observed for row in rows)
    assert result["exceedances"]==count
    assert result["p_upper_plus_one"]==(1+count)/10000
    np.testing.assert_allclose(result["tail_probability_ci95"],
        [beta.ppf(.025,count,9999-count+1),beta.ppf(.975,count+1,9999-count)])
frozen.OUT=OUT
result={"status":"passed","dense_circuits_checked":10000,"null_round_scores":9999*505,
    "max_mean_log_gain_error":max_error,"unchanged_prefix_histories":999,"actual_unchanged":True,
    "archive_blocks":len(manifest),"wall_seconds":time.monotonic()-started,"cpu_seconds":time.process_time()-cpu,
    "peak_memory_mib":frozen.peak_memory()/1024**2}
frozen.save("verification.json",result)
print(result)
