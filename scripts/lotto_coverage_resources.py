"""Exact five-ticket coverage; bounded-memory exhaustive verification."""
import os
for key in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
    os.environ[key] = '1'
import ctypes
import hashlib
import itertools
import json
import math
import time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/lotto/coverage-study'
OLD = [[13,20,21,22,32,42],[4,5,6,13,16,18],[3,4,15,16,28,32],
       [2,5,6,15,28,38],[18,19,21,25,31,42]]
NEW = [[2,8,15,23,31,40],[4,11,18,26,34,43],[6,13,20,28,36,45],
       [1,9,17,25,33,41],[3,12,21,29,37,44]]

def constrain():
    if os.name == 'nt':
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetCurrentProcess.restype = ctypes.c_void_p
        h = k.GetCurrentProcess()
        a,b = ctypes.c_size_t(),ctypes.c_size_t()
        k.GetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p]
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
        k.SetPriorityClass.argtypes = [ctypes.c_void_p,ctypes.c_uint]
        assert k.GetProcessAffinityMask(h,ctypes.byref(a),ctypes.byref(b))
        assert k.SetProcessAffinityMask(h,a.value & -a.value)
        assert k.SetPriorityClass(h,0x4000)
    else:
        os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
        os.nice(10)

def peak_mib():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    class Counters(ctypes.Structure):
        _fields_ = [('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)] + [(n,ctypes.c_size_t) for n in ('peak','working','ppp','pp','pnp','np','page','peakpage')]
    k=ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype=ctypes.c_void_p
    fn=ctypes.WinDLL('psapi').GetProcessMemoryInfo
    fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    c=Counters();c.cb=ctypes.sizeof(c)
    assert fn(k.GetCurrentProcess(),ctypes.byref(c),c.cb)
    return c.peak/1024**2

def count(portfolios, n=45, k=6, batch=25000, pause=None):
    tables={}
    for name,tickets in portfolios.items():
        table=np.zeros((n+1,len(tickets)),dtype=np.uint8)
        for j,ticket in enumerate(tickets):
            assert len(ticket)==k and len(set(ticket))==k
            assert all(1<=v<=n for v in ticket)
            table[ticket,j]=1
        tables[name]=table
    hist={name:np.zeros(k+1,dtype=np.int64) for name in tables}
    wins={name:{t:np.zeros(len(p)+1,dtype=np.int64) for t in (3,4)} for name,p in portfolios.items()}
    stream=itertools.combinations(range(1,n+1),k); total=0
    while True:
        start=time.monotonic(); rows=list(itertools.islice(stream,batch))
        if not rows:break
        draws=np.asarray(rows,dtype=np.uint8);total+=len(rows)
        for name,table in tables.items():
            matches=table[draws].sum(axis=1)
            hist[name]+=np.bincount(matches.max(axis=1).astype(int),minlength=k+1)
            for t in (3,4):
                wins[name][t]+=np.bincount((matches>=t).sum(axis=1),minlength=len(portfolios[name])+1)
        if pause:pause(start,total)
    assert total==math.comb(n,k)
    return {name:{'maximum_match_histogram':h.tolist(),
                  'coverage_counts':{str(t):int(h[t:].sum()) for t in range(1,k+1)},
                  'winning_ticket_count_histograms':{str(t):v.tolist() for t,v in wins[name].items()}}
            for name,h in hist.items()}

def disjoint_formula(n=45,k=6,m=5):
    single={t:sum(math.comb(k,j)*math.comb(n-k,k-j) for j in range(t,k+1)) for t in (3,4)}
    assert k==6 and m*k<=n
    return {'3':m*single[3]-math.comb(m,2)*math.comb(6,3)**2,'4':m*single[4]}

def main():
    constrain();start=time.monotonic();cpu=time.process_time();OUT.mkdir(parents=True,exist_ok=True)
    config={'previous':OLD,'optimal_disjoint':NEW}
    protocol={'portfolios':config,'n':45,'draw_size':6,'objectives':['any_match_ge_3','any_match_ge_4'],
              'model':'uniform six-element winning set; no historical training',
              'resources':'one CPU, BelowNormal, batch25000, >=50% compute rest, 384MiB/600s guards',
              'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    def pause(t,total):
        assert time.monotonic()-start<600 and peak_mib()<384
        time.sleep(max(.001,.5*(time.monotonic()-t)))
    result=count(config,pause=pause)
    exact=disjoint_formula()
    for t,value in exact.items():assert result['optimal_disjoint']['coverage_counts'][t]==value
    N=math.comb(45,6)
    for name,r in result.items():
        assert sum(r['maximum_match_histogram'])==N
        assert r['coverage_counts']['6']==5
        for t,h in r['winning_ticket_count_histograms'].items():
            expected=5*sum(math.comb(6,j)*math.comb(39,6-j) for j in range(int(t),7))
            assert sum(i*v for i,v in enumerate(h))==expected
        r['probabilities']={t:v/N for t,v in r['coverage_counts'].items()}
    payload={'status':'verified','outcomes':N,'results':result,'disjoint_formula':exact,
             'wall_seconds':time.monotonic()-start,'cpu_seconds':time.process_time()-cpu,'peak_mib':peak_mib()}
    (OUT/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    print(json.dumps(payload))

if __name__=='__main__':main()
