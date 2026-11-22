"""Stream all 99,999 circuits through an independent dense-gate calculation."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import lotto_small_quantum as frozen
from lotto_quantum_99999 import OUT,PREFIX,read,iter_results,lock_process
import math
import time
import numpy as np

lock=lock_process();frozen.constrain_process();frozen.OUT=OUT
started=time.monotonic();cpu=time.process_time();frozen.DEADLINE=started+3600
protocol=read(OUT/"protocol.json");summary=read(OUT/"summary.json")
assert summary["complete"] and summary["total_null_histories"]==99999
actual=read(OUT/"actual.json");assert actual==read(PREFIX/"actual.json")
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
    w=frozen.BASE*state**2
    return .05*frozen.BASE+.95*w/w.sum()


max_error=0.;count=0;exceed={s:0 for s in actual["splits"]}
for i,row in iter_results(protocol):
    before=time.monotonic();frozen.guard()
    p=dense_probabilities(row["fit"]["theta"])
    rng=np.random.default_rng(2026101700+i)
    labels=rng.choice(64,1245,p=frozen.BASE)
    gains=np.log(p[labels]/frozen.BASE[labels])
    for name,lo,hi in (("validation",740,990),("final",990,1238),("seen_extension",1238,1245)):
        saved=row["splits"][name]["mean_log_gain"]
        max_error=max(max_error,abs(float(gains[lo:hi].mean())-saved))
        exceed[name]+=int(saved>=actual["splits"][name]["mean_log_gain"])
    count+=1
    if count%1000==0:
        frozen.save("verification-progress.json",{"checked":count,"target":99999,"updated_unix":time.time()})
        print(f"dense verification {count}/99999",flush=True)
    time.sleep(max(.002,time.monotonic()-before))
assert count==99999 and max_error<1e-12
np.testing.assert_allclose(dense_probabilities(actual["fit"]["theta"]),actual["category_probabilities"],atol=1e-14)
for s,n in exceed.items():
    assert summary["comparisons"][s]["exceedances"]==n
    assert summary["comparisons"][s]["p_upper_plus_one"]==(n+1)/100000
result={"status":"passed","dense_circuits":100000,"null_evaluation_rounds":99999*505,
    "maximum_mean_log_gain_error":max_error,"exceedances":exceed,
    "elapsed_seconds":time.monotonic()-started,"cpu_seconds":time.process_time()-cpu,
    "peak_memory_mib":frozen.peak_memory()/1024**2}
frozen.save("verification.json",result)
frozen.save("progress.json",{"state":"verified","completed":99999,"target":99999,"updated_unix":time.time()})
print(result,flush=True)
