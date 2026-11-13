"""Bounded, preregistered-in-code exploratory power and temporal study."""
import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'
import ctypes
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
from scipy.optimize import brentq
from scipy.stats import beta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/lotto/entropy-study'
N=1245
TRAIN=740
P=6/45
K=np.arange(7)
BASE=np.array([math.comb(6,k)*math.comb(39,6-k)/math.comb(45,6) for k in K])
PROTOCOL={'seed':2026101701,'n':N,'train':TRAIN,'scale_reference':200,'null_calibration':999,
 'null_validation':200,'permutations':999,'power_replicates':200,
 'injection_strengths':[0,.1,.25,.5,1.],
 'models':['overlap exponential tilt vs uniform','ball 1 Markov vs ball 1 iid'],
 'smoothing':100,'theta_bounds':[-2,2],'alpha':.05,
 'family_test':'max of two null-standardized held-out log gains; plus-one tail',
 'gate':'family permutation p<=.05 and iid p<=.05 and selected held-out gain>0 and copy-one power at strength 1 >=.8',
 'future':'only prospective uninspected draws after registration; no retrospective e-value claim',
 'resources':'one logical CPU, BelowNormal, batch 20, rest >=25% compute, 30 minute wall budget'}

def constrain():
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        h=kernel.GetCurrentProcess()
        kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
        a,b=ctypes.c_size_t(),ctypes.c_size_t()
        assert kernel.GetProcessAffinityMask(h,ctypes.byref(a),ctypes.byref(b))
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint]
        assert kernel.SetProcessAffinityMask(h,a.value & -a.value)
        assert kernel.SetPriorityClass(h,0x4000)
    else:os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})

def overlap_distribution(theta):
    w=BASE*np.exp(theta*K)
    return w/w.sum()

def score(draws):
    """Only first TRAIN draws fit parameters. Output is held-out total nats."""
    overlap=np.sum(draws[1:] & draws[:-1],axis=1)
    target=(overlap[:TRAIN-1].sum()+100*.8)/(TRAIN-1+100)
    lo,hi=-2.,2.
    theta=lo if target<=overlap_distribution(lo)@K else hi if target>=overlap_distribution(hi)@K else brentq(lambda t:overlap_distribution(t)@K-target,lo,hi)
    z=np.sum(BASE*np.exp(theta*K))
    gain1=float(np.sum(theta*overlap[TRAIN-1:]-np.log(z)))
    x=draws[:,0].astype(int)
    iid=(x[:TRAIN].sum()+100*P)/(TRAIN+100)
    probabilities=[]
    for previous in (0,1):
        mask=x[:TRAIN-1]==previous
        probabilities.append(float((x[1:TRAIN][mask].sum()+100*iid)/(mask.sum()+100)))
    pred=np.array(probabilities)[x[TRAIN-1:-1]];y=x[TRAIN:]
    gain2=float(np.sum(y*np.log(pred/iid)+(1-y)*np.log((1-pred)/(1-iid))))
    return np.array([gain1,gain2]),{'theta':theta,'iid_ball1':float(iid),'conditional_ball1':probabilities}

def generate(rng,batch,kind='iid',strength=0):
    # Uniform subset via iid random ranks; injection forces one target then
    # replaces a uniformly selected existing ball if absent. Always six unique.
    ranks=rng.random((batch,N,45))
    indices=np.argpartition(ranks,5,axis=2)[:,:,:6]
    del ranks
    draws=np.zeros((batch,N,45),dtype=bool)
    np.put_along_axis(draws,indices,True,axis=2)
    if kind!='iid' and strength:
        for t in range(0 if kind=='static' else 1,N):
            for b in np.flatnonzero(rng.random(batch)<strength):
                target=0 if kind=='static' else int(rng.choice(np.flatnonzero(draws[b,t-1])))
                if not draws[b,t,target]:
                    remove=int(rng.choice(np.flatnonzero(draws[b,t])))
                    draws[b,t,remove]=False;draws[b,t,target]=True
    return draws

def tail(ref,value):return float((1+np.count_nonzero(ref>=value))/(len(ref)+1))

def interval(c,n):
    return [0. if c==0 else float(beta.ppf(.025,c,n-c+1)),1. if c==n else float(beta.ppf(.975,c+1,n-c))]

def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
    constrain();started=time.monotonic();cpu=time.process_time()
    protocol=dict(PROTOCOL)
    protocol['source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    protocol['data_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (OUT/'base.jsonl',OUT/'extra.jsonl')}
    save('protocol.json',protocol) # Persist before evaluating actual outcomes.
    rng=np.random.default_rng(PROTOCOL['seed'])
    def simulate(n,kind='iid',strength=0):
        values=[]
        for start in range(0,n,20):
            assert time.monotonic()-started<1800,'Wall budget exceeded'
            t=time.monotonic()
            values.extend(score(d)[0] for d in generate(rng,min(20,n-start),kind,strength))
            time.sleep(max(.01,.25*(time.monotonic()-t)))
        return np.array(values)
    scale=simulate(200);mean=scale.mean(axis=0);sd=scale.std(axis=0,ddof=1)
    null=simulate(999)
    ref=((null-mean)/sd).max(axis=1)
    def check(values):
        p=[tail(ref,v) for v in ((values-mean)/sd).max(axis=1)]
        c=sum(v<=.05 for v in p)
        return {'detections':c,'n':len(p),'rate':c/len(p),'ci95':interval(c,len(p))}
    validation=check(simulate(200));power={}
    print('null calibration and independent validation complete',flush=True)
    for kind in ('static','copy_one'):
        power[kind]={}
        for strength in PROTOCOL['injection_strengths']:
            power[kind][str(strength)]=check(simulate(200,kind,strength))
            print(kind,strength,power[kind][str(strength)],flush=True)
    rows=[json.loads(line) for name in ('base.jsonl','extra.jsonl') for line in (OUT/name).read_text(encoding='utf-8').splitlines()]
    assert [r['draw_no'] for r in rows]==list(range(1,N+1))
    draws=np.zeros((N,45),dtype=bool)
    for i,row in enumerate(rows):
        numbers=row['numbers_sorted'];assert len(set(numbers))==6 and all(1<=n<=45 for n in numbers)
        draws[i,np.array(numbers)-1]=True
    actual,params=score(draws);value=float(((actual-mean)/sd).max())
    shuffled=[]
    for i in range(999):
        t=time.monotonic();shuffled.append(score(draws[rng.permutation(N)])[0])
        time.sleep(max(.001,.25*(time.monotonic()-t)))
    shuffled=np.array(shuffled)
    perm_p=tail(((shuffled-mean)/sd).max(axis=1),value);iid_p=tail(ref,value)
    selected=int(np.argmax((actual-mean)/sd))
    gate=perm_p<=.05 and iid_p<=.05 and actual[selected]>0 and power['copy_one']['1.0']['rate']>=.8
    np.savez_compressed(OUT/'scores.npz',scale=scale,null=null,permutation=shuffled,actual=actual)
    result={'status':'completed','null_validation':validation,'power':power,'actual_total_log_gain':actual.tolist(),
      'actual_bits_per_draw':(actual/505/np.log(2)).tolist(),'parameters':params,'iid_family_p':iid_p,'permutation_family_p':perm_p,
      'selected_model_index':selected,'prospective_gate_passed':bool(gate),'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu}
    save('results.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()

