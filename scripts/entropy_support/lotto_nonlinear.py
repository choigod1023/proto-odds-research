"""Small fixed categorical interaction models; full-search permutation control."""
import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
import ctypes
import hashlib
import json
import time
from pathlib import Path
import numpy as np
from scipy.stats import beta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/lotto/nonlinear-study'
N=1245;TRAIN=740;WARM=10
TARGETS=['odd_count_parity','low_count_parity','sum_band','has_adjacent_numbers']
SIZES=[2,2,5,2]
CONTEXTS=['own_lags_1_2','own_lags_1_2_3','lag1_lag2_parity_xor','previous5_high_count','cross_odd_low_lag1','own_lags_5_10']
PROTOCOL={'seed':2026101703,'draws':N,'train_end':TRAIN,'warmup':WARM,
 'targets':TARGETS,'sum_band_edges':[100,120,150,170],'contexts':CONTEXTS,
 'smoothing':20,'tests':24,'null_permutations':999,'iid_histories':999,
 'decision':'max held-out total log gain; plus-one family p <= .05 and positive gain',
 'power_replicates':100,'power_permutations':99,'xor_strengths':[0,.25,.5],
 'limits':'one CPU, BelowNormal, BLAS one thread, >=25% rest, 600 seconds wall',
 'interpretation':'exploratory previously inspected data; not full combination prediction'}

def constrain():
    if os.name=='nt':
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=ctypes.c_void_p
        h=k.GetCurrentProcess();a,b=ctypes.c_size_t(),ctypes.c_size_t()
        k.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
        k.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        k.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint]
        assert k.GetProcessAffinityMask(h,ctypes.byref(a),ctypes.byref(b))
        assert k.SetProcessAffinityMask(h,a.value & -a.value)
        assert k.SetPriorityClass(h,0x4000)
    else:os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})

def peak_mib():
    if os.name!='nt':return None
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak','working','ppp','pp','pnp','np','page','peakpage')]
    k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=ctypes.c_void_p
    fn=ctypes.WinDLL('psapi').GetProcessMemoryInfo;fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    c=Counters();c.cb=ctypes.sizeof(c);assert fn(k.GetCurrentProcess(),ctypes.byref(c),c.cb)
    return c.peak/1024**2

def features(draws):
    return np.column_stack(((draws%2).sum(1)%2,(draws<=22).sum(1)%2,
       np.digitize(draws.sum(1),[100,120,150,170]),(np.diff(np.sort(draws,axis=1),axis=1)==1).any(1))).astype(int)

def uniform(rng,n):
    return np.argpartition(rng.random((n,45)),5,axis=1)[:,:6]+1

def contexts(f,j):
    y=f[:,j];k=SIZES[j];t=np.arange(WARM,len(f))
    return [y[t-1]*k+y[t-2],(y[t-1]*k+y[t-2])*k+y[t-3],
       (y[t-1]%2)^(y[t-2]%2),sum(y[t-d]>=k//2 for d in range(1,6)),
       f[t-1,0]*2+f[t-1,1],y[t-5]*k+y[t-10]]

def scores(f,details=False):
    values=[];params=[];cut=TRAIN-WARM
    for j,k in enumerate(SIZES):
        y=f[WARM:,j]
        counts=np.bincount(y[:cut],minlength=k);base=(counts+1)/(cut+k)
        for ctx in contexts(f,j):
            # Fixed context universe prevents future categories changing the fit.
            count=np.bincount(ctx[:cut]*k+y[:cut],minlength=125*k).reshape(125,k)
            probs=(count+20*base)/(count.sum(1)[:,None]+20)
            gain=np.log(probs[ctx[cut:],y[cut:]]/base[y[cut:]])
            values.append(float(gain.sum()))
            if details:params.append({'base':base.tolist(),'probabilities':probs.tolist(),
                'early_gain':float(gain[:250].sum()),'late_gain':float(gain[250:].sum())})
    return (np.array(values),params) if details else np.array(values)

def tail(a,x):return float((1+np.count_nonzero(np.asarray(a)>=x))/(len(a)+1))

def ci(c,n):return [0. if c==0 else float(beta.ppf(.025,c,n-c+1)),1. if c==n else float(beta.ppf(.975,c+1,n-c))]

def injected(rng,strength):
    """At selected steps force odd-count parity to XOR of previous two.
    Rejection sampling preserves uniform subsets within the requested parity.
    """
    d=uniform(rng,N);parity=features(d)[:,0]
    for t in range(2,N):
        if rng.random()<strength:
            target=parity[t-1]^parity[t-2]
            while parity[t]!=target:
                d[t]=uniform(rng,1)[0];parity[t]=int((d[t]%2).sum()%2)
    return d

def save(name,data):(OUT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
    constrain();start=time.monotonic();cpu=time.process_time()
    protocol=dict(PROTOCOL,source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
       data_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [OUT/'base.jsonl',OUT/'extra.jsonl']})
    save('protocol.json',protocol)
    rng=np.random.default_rng(PROTOCOL['seed'])
    def pause(t):
        assert time.monotonic()-start<600,'Wall limit exceeded'
        time.sleep(max(.0005,.25*(time.monotonic()-t)))
    power={}
    for strength in PROTOCOL['xor_strengths']:
        hits=0
        for i in range(100):
            t=time.monotonic();f=features(injected(rng,strength));actual=scores(f).max()
            ref=[scores(f[rng.permutation(N)]).max() for _ in range(99)]
            hits+=tail(ref,actual)<=.05 and actual>0;pause(t)
        power[str(strength)]={'hits':int(hits),'n':100,'rate':hits/100,'ci95':ci(hits,100)}
        print('power',strength,power[str(strength)],flush=True)
    rows=[json.loads(line) for name in ('base.jsonl','extra.jsonl') for line in (OUT/name).read_text(encoding='utf-8').splitlines()]
    assert [r['draw_no'] for r in rows]==list(range(1,N+1))
    d=np.array([r['numbers_sorted'] for r in rows]);assert d.shape==(N,6)
    assert ((d>=1)&(d<=45)).all() and (np.diff(np.sort(d,axis=1),axis=1)>0).all()
    f=features(d);actual,params=scores(f,True)
    perm=[];iid=[]
    for i in range(999):
        t=time.monotonic();perm.append(scores(f[rng.permutation(N)]));iid.append(scores(features(uniform(rng,N))));pause(t)
    perm=np.array(perm);iid=np.array(iid);best=int(actual.argmax())
    results=[]
    for index,gain in enumerate(actual):
        results.append({'target':TARGETS[index//6],'context':CONTEXTS[index%6],
          'total_log_gain':float(gain),'bits_per_draw':float(gain/(N-TRAIN)/np.log(2)),
          'early_gain':params[index]['early_gain'],'late_gain':params[index]['late_gain'],
          'permutation_p_unadjusted':tail(perm[:,index],gain),
          'permutation_p_family':tail(perm.max(1),gain)})
    result={'status':'completed','power':power,'models':results,'best_index':best,
       'family_permutation_p':tail(perm.max(1),actual.max()),'family_iid_p':tail(iid.max(1),actual.max()),
       'positive_best_gain':bool(actual.max()>0),'wall_seconds':time.monotonic()-start,
       'cpu_seconds':time.process_time()-cpu,'peak_working_set_mib_this_process':peak_mib()}
    np.savez_compressed(OUT/'scores.npz',actual=actual,permutations=perm,iid=iid)
    save('results.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
