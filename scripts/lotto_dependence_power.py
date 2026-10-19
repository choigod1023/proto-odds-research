"""Prospectively fixed power grid for the unchanged conditional test.

All cases have uniform single-number marginals. Pure-triple cases also
preserve every pair probability, isolating a higher-order dependence.
"""
import lotto_combination_dependence as old
import ctypes
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import numpy as np
from scipy.stats import beta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/lotto/dependence-power'
BASE_SEED=202610174001
REPEATS=30
LEVELS={'pair':[1.25,1.5,2,3,4], 'pure_triple':[1.5,2,3,5,8],
        'diffuse':[.005,.01,.02,.05,.1]}

def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True);path=OUT/name;tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(path)

class Native:
    def __init__(self):
        folder=ROOT/'.local-build';folder.mkdir(exist_ok=True)
        self.path=folder/('curveball_power.dll' if os.name=='nt' else 'curveball_power.so')
        compiler=shutil.which('g++') or shutil.which('clang++')
        if not compiler:raise RuntimeError('C++ compiler required for fixed-protocol power study')
        source=ROOT/'scripts/curveball_power.cpp'
        if not self.path.exists() or self.path.stat().st_mtime<source.stat().st_mtime:
            flags=['-O3','-std=c++17','-shared']+(['-static-libgcc','-static-libstdc++'] if os.name=='nt' else ['-fPIC'])
            subprocess.run([compiler,*flags,str(source),'-o',str(self.path)],check=True,capture_output=True)
        self.directory_handle=os.add_dll_directory(str(Path(compiler).parent)) if os.name=='nt' else None
        self.lib=ctypes.CDLL(str(self.path))
        u64=np.ctypeslib.ndpointer(dtype=np.uint64,flags='C_CONTIGUOUS')
        u16=np.ctypeslib.ndpointer(dtype=np.uint16,flags='C_CONTIGUOUS')
        self.lib.endpoint.argtypes=[u64,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint64,u64]
        self.lib.spoke_counts.argtypes=[u64,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint64,u16]

    def walk(self,rows,steps,seed,block=200):
        out=np.empty_like(rows)
        assert self.lib.endpoint(rows,len(rows),steps,block,seed,out)==0
        return out

    def sample(self,rows,seed,pause=lambda tick:None):
        original=np.array([sum(1<<v for v in row) for row in rows],dtype=np.uint64)
        hub=self.walk(original,20000,seed)
        matrix=np.empty((1000,15180),dtype=np.uint16);matrix[0]=old.counts(rows)
        rng=random.Random(seed+1)
        for start in range(1,1000,25):
            tick=time.monotonic();number=min(25,1000-start)
            assert self.lib.spoke_counts(hub,1000,number,20000,200,rng.getrandbits(64),matrix[start:start+number])==0
            pause(tick)
        assert np.all(matrix[:,:990].sum(1)==15000) and np.all(matrix[:,990:].sum(1)==20000)
        return matrix

def category_probabilities(kind,strength):
    k=2 if kind=='pair' else 3
    base=np.array([math.comb(k,j)*math.comb(45-k,6-j)/math.comb(45,6) for j in range(k+1)])
    q=base.copy();delta=base[-1]*(strength-1)
    q+=delta*(np.array([1,-2,1]) if k==2 else np.array([-1,3,-3,1]))
    assert q.min()>=0 and np.isclose(q.sum(),1)
    return base,q

def generate(kind,strength,seed,n=1000):
    rng=random.Random(seed);rows=[]
    if kind=='null':
        rows=[set(rng.sample(range(45),6)) for _ in range(n)];target=None
    elif kind=='diffuse':
        # Random label permutation makes group labels arbitrary, not fitted.
        labels=list(range(45));rng.shuffle(labels)
        for _ in range(n):
            if rng.random()<strength:
                group=rng.randrange(5);row=set(rng.sample(labels[9*group:9*group+9],6))
            else:row=set(rng.sample(range(45),6))
            rows.append(row)
        target=None
    else:
        k=2 if kind=='pair' else 3
        target=tuple(sorted(rng.sample(range(45),k)));outside=sorted(set(range(45))-set(target))
        _,q=category_probabilities(kind,strength)
        for _ in range(n):
            j=rng.choices(range(k+1),weights=q,k=1)[0]
            rows.append(set(rng.sample(target,j))|set(rng.sample(outside,6-j)))
    return rows,target

def exact_interval(k,n):
    return [0.0 if k==0 else float(beta.ppf(.025,k,n-k+1)),
            1.0 if k==n else float(beta.ppf(.975,k+1,n-k))]

def effect(kind,strength):
    if kind=='null':return {'description':'no dependence; false-positive check'}
    if kind=='diffuse':
        pp=math.comb(6,2)/math.comb(45,2);pt=math.comb(6,3)/math.comb(45,3)
        return {'mixture_fraction':strength,'within_group_pair_multiplier':((1-strength)*pp+strength/12)/pp,
                'within_group_triple_multiplier':((1-strength)*pt+strength/21)/pt}
    base,q=category_probabilities(kind,strength)
    return {'target_multiplier':strength,'expected_target_count_per_1000':1000*q[-1],
            'null_expected_target_count_per_1000':1000*base[-1],
            'target_category_probabilities':q.tolist()}

def jobs():
    all_jobs=[('null',1,i) for i in range(60)]
    for kind,levels in LEVELS.items():
        for level in levels:all_jobs += [(kind,level,i) for i in range(REPEATS)]
    return all_jobs

def summarize(records):
    grouped={}
    for r in records:grouped.setdefault((r['kind'],r['strength']),[]).append(r)
    cells=[]
    for (kind,strength),rs in grouped.items():
        cell={'kind':kind,'strength':strength,'n':len(rs),'effect':effect(kind,strength)}
        for outcome in ('global_detected','local_gate','target_identified'):
            if outcome=='target_identified' and kind in ('null','diffuse'):continue
            k=sum(r[outcome] for r in rs)
            cell[outcome]={'count':k,'rate':k/len(rs),'ci95_pointwise':exact_interval(k,len(rs))}
        cells.append(cell)
    return cells

def run():
    old.resources.constrain();start=time.monotonic();cpu=time.process_time();native=Native()
    schedule=jobs();OUT.mkdir(parents=True,exist_ok=True)
    paths=[ROOT/'scripts'/p for p in ('lotto_dependence_power.py','curveball_power.cpp','lotto_combination_dependence.py','lotto_coverage_resources.py')]
    protocol={'jobs':len(schedule),'repeats_per_alternative':REPEATS,'null_repeats':60,'levels':LEVELS,
              'histories_per_dataset':1000,'block_size':200,'spokes':999,'steps':20000,
              'test':'unchanged Python analyze(): pair/triple dispersion,15180-feature maxT,Bonferroni3; local gate<=.05',
              'primary_power':'local gate used to promote the prior prediction rule',
              'secondary_power':'any global adjusted p<=.05 and injected target identification',
              'alternative':'iid draws; pair keeps singles; pure_triple keeps singles and all pairs; diffuse keeps singles',
              'seed_base':BASE_SEED,'interval':'Clopper-Pearson exact95% pointwise; not simultaneous; no effect exclusion confidence bound',
              'resources':'one CPU BelowNormal; >=50% rest after native batches and Python analysis;384MiB/1800s per invocation guards',
              'native':'same reversible kernel law, different PRNG draws from prior Python run; compiled single-thread C++',
              'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    protocol_path=OUT/'protocol.json'
    if protocol_path.exists():assert json.loads(protocol_path.read_text())==protocol,'protocol changed; resume forbidden'
    else:save('protocol.json',protocol)
    records=json.loads((OUT/'records.json').read_text()) if (OUT/'records.json').exists() else []
    initial_record_count=len(records)
    def pause(tick):
        assert time.monotonic()-start<1800,'session wall guard'
        assert old.resources.peak_mib()<384,'working-set guard'
        time.sleep(max(.001,.5*(time.monotonic()-tick)))
    def progress():
        save('progress.json',{'state':'running','completed':len(records),'target':len(schedule),
                             'wall_seconds_this_invocation':time.monotonic()-start,'peak_mib_this_process':old.resources.peak_mib()})
    progress()
    for index,(kind,strength,rep) in enumerate(schedule):
        if index<len(records):
            assert (records[index]['kind'],records[index]['strength'],records[index]['replicate'])==(kind,strength,rep)
            continue
        seed=BASE_SEED+10000*index;rows,target=generate(kind,strength,seed)
        matrix=native.sample(rows,seed+100,pause)
        tick=time.monotonic();analysis,selected=old.analyze(matrix)
        target_index=old.FEATURES.index(target) if target is not None else None
        record={'index':index,'kind':kind,'strength':strength,'replicate':rep,'generation_seed':seed,
                'sampling_seed':seed+100,'target':[v+1 for v in target] if target is not None else None,
                'target_count':int(matrix[0,target_index]) if target_index is not None else None,
                'global_p':{k:v['adjusted_p'] for k,v in analysis['global_tests'].items()},
                'global_detected':any(v['adjusted_p']<=.05 for v in analysis['global_tests'].values()),
                'local_gate':bool(selected),'selected_count':len(selected),
                'target_identified':target_index in selected if target_index is not None else False,
                'counts_sha256':hashlib.sha256(matrix.tobytes()).hexdigest()}
        if index==0:np.savez_compressed(OUT/'first_null_counts.npz',counts=matrix)
        del matrix
        pause(tick);records.append(record);save('records.json',records);progress()
        if len(records)%30==0 or index==59:
            save('partial-summary.json',summarize(records));print('completed',len(records),'/',len(schedule),flush=True)
    resources={'wall_seconds':time.monotonic()-start,'cpu_seconds':time.process_time()-cpu,'peak_mib':old.resources.peak_mib(),
               'records_at_start':initial_record_count}
    # Resource times describe this invocation, not previous/resumed invocations.
    save('results.json',{'status':'completed','records':len(records),'cells':summarize(records),'resources_this_invocation':resources})
    save('progress.json',{'state':'completed','completed':len(records),'target':len(schedule)})
    print(json.dumps(resources),flush=True)

if __name__=='__main__':run()
