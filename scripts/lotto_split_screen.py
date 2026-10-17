"""Fixed prospective synthetic comparison: screen 800, confirm 445, total 1245.

No optimization of the split, shortlist length or threshold from outcomes.
Real-data follow-up is allowed only by the saved simulation gate.
"""
import lotto_dependence_power as prior  # constrains BLAS before NumPy import
import ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
import numpy as np
from scipy.stats import beta, binomtest

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/lotto/split-screen'
SEED=202610175001
LEVELS={'pair':[1.5,2,3], 'pure_triple':[3,5,8]}
WEAK={('pair',1.5),('pair',2),('pure_triple',3),('pure_triple',5)}
SOURCES=['scripts/lotto_split_screen.py','scripts/split_screen_kernel.cpp',
         'scripts/curveball_power.cpp','scripts/lotto_dependence_power.py',
         'scripts/lotto_combination_dependence.py','scripts/lotto_coverage_resources.py']

def digest(data):return hashlib.sha256(data).hexdigest()
def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/name;tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n');tmp.replace(p)
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def jobs():
    result=[('null',1,i) for i in range(100)]
    for kind,levels in LEVELS.items():
        for level in levels:result.extend((kind,level,i) for i in range(30))
    return result

class Native:
    def __init__(self):
        folder=ROOT/'.local-build';folder.mkdir(exist_ok=True)
        self.path=folder/('split_screen.dll' if os.name=='nt' else 'split_screen.so')
        compiler=shutil.which('g++') or shutil.which('clang++')
        if not compiler:raise RuntimeError('C++17 compiler required')
        sources=[ROOT/'scripts'/f for f in ('split_screen_kernel.cpp','curveball_power.cpp')]
        if not self.path.exists() or any(p.stat().st_mtime>self.path.stat().st_mtime for p in sources):
            flags=['-O3','-std=c++17','-shared']+(['-static-libgcc','-static-libstdc++'] if os.name=='nt' else ['-fPIC'])
            subprocess.run([compiler,*flags,str(sources[0]),'-o',str(self.path)],check=True,capture_output=True)
        self.directory=os.add_dll_directory(str(Path(compiler).parent)) if os.name=='nt' else None
        self.lib=ctypes.CDLL(str(self.path))
        u64=np.ctypeslib.ndpointer(dtype=np.uint64,flags='C_CONTIGUOUS')
        u16=np.ctypeslib.ndpointer(dtype=np.uint16,flags='C_CONTIGUOUS')
        i32=np.ctypeslib.ndpointer(dtype=np.int32,flags='C_CONTIGUOUS')
        self.lib.split_hub.argtypes=[u64,ctypes.c_int,i32,ctypes.c_int,ctypes.c_int,ctypes.c_uint64,u64]
        self.lib.split_spokes.argtypes=[u64,ctypes.c_int,i32,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint64,u16]

    def sample(self,rows,ends,reps,seed,pause=lambda tick:None):
        n=len(rows);ends=np.array(ends,dtype=np.int32)
        assert len(ends)>0 and ends[-1]==n and np.all(np.diff(np.r_[0,ends])>=2)
        original=np.array([sum(1<<v for v in row) for row in rows],dtype=np.uint64)
        hub=np.empty_like(original);steps=20*n
        assert self.lib.split_hub(original,n,ends,len(ends),steps,seed,hub)==0
        matrix=np.empty((reps+1,15180),dtype=np.uint16);matrix[0]=prior.old.counts(rows)
        rng=random.Random(seed+1)
        for start in range(1,reps+1,25):
            tick=time.monotonic();count=min(25,reps+1-start)
            assert self.lib.split_spokes(hub,n,ends,len(ends),count,steps,rng.getrandbits(64),matrix[start:start+count])==0
            pause(tick)
        assert np.all(matrix[:,:990].sum(1)==15*n) and np.all(matrix[:,990:].sum(1)==20*n)
        return matrix

def screen(matrix):
    means=matrix.mean(0);sd=np.sqrt(np.maximum(matrix.var(0),.25))
    z=(matrix[0]-means)/sd
    indices=[int(np.argmax(np.abs(z[:990]))),990+int(np.argmax(np.abs(z[990:])))]
    return [{'index':j,'numbers':[v+1 for v in prior.old.FEATURES[j]],
             'direction':1 if z[j]>=0 else -1,'training_z':float(z[j])} for j in indices]

def confirm(matrix,candidates):
    # Candidate and sign are fixed using disjoint training draws. Conditional
    # endpoints (including observed) are exchangeable under the validation null.
    result=[]
    for c in candidates:
        values=matrix[:,c['index']].astype(float)*c['direction']
        p=float(np.mean(values>=values[0]));adjusted=min(1.,len(candidates)*p)
        result.append({**c,'raw_p':p,'adjusted_p':adjusted,'confirmed':adjusted<=.05,
                       'validation_count':int(matrix[0,c['index']])})
    return result

def full_test(matrix,target_index):
    means=matrix.mean(0);sd=np.sqrt(np.maximum(matrix.var(0),.25));maximum=np.zeros(len(matrix))
    for start in range(0,15180,256):
        z=np.abs((matrix[:,start:start+256]-means[start:start+256])/sd[start:start+256])
        maximum=np.maximum(maximum,z.max(1))
    omnibus=float(np.mean(maximum>=maximum[0]))
    p=None if target_index is None else float(np.mean(maximum>=abs((matrix[0,target_index]-means[target_index])/sd[target_index])))
    return {'any_p':omnibus,'any':omnibus<=.05,'target_p':p,'target':p is not None and p<=.05}

def evaluate(index,native,pause):
    kind,strength,rep=jobs()[index];seed=SEED+10000*index
    rows,target=prior.generate(kind,strength,seed,n=1245)
    target_index=None if target is None else prior.old.FEATURES.index(target)
    hashes={}
    train=native.sample(rows[:800],[200,400,600,800],199,seed+1,pause)
    tick=time.monotonic();candidates=screen(train);hashes['train']=digest(train.tobytes());del train;pause(tick)
    validation=native.sample(rows[800:],[445],999,seed+2,pause)
    tick=time.monotonic();confirmed=confirm(validation,candidates);hashes['validation']=digest(validation.tobytes());del validation;pause(tick)
    full=native.sample(rows,[200,400,600,800,1245],999,seed+3,pause)
    tick=time.monotonic();baseline=full_test(full,target_index);hashes['full']=digest(full.tobytes());del full;pause(tick)
    return {'index':index,'kind':kind,'strength':strength,'replicate':rep,'seed':seed,
            'target_index':target_index,'candidates':confirmed,
            'screen_target':any(c['index']==target_index for c in candidates),
            'split_any':any(c['confirmed'] for c in confirmed),
            'split_target':any(c['index']==target_index and c['confirmed'] for c in confirmed),
            'full_any':baseline['any'],'full_target':baseline['target'],
            'full_any_p':baseline['any_p'],'full_target_p':baseline['target_p'],'matrix_sha256':hashes}

def gate(records):
    controls=[r for r in records if r['kind']=='null']
    weak=[r for r in records if (r['kind'],r['strength']) in WEAK]
    n=sum(r['split_any'] for r in controls)
    upper=1. if n==len(controls) else float(beta.ppf(.95,n+1,len(controls)-n))
    wins=sum(r['split_target'] and not r['full_target'] for r in weak)
    losses=sum(r['full_target'] and not r['split_target'] for r in weak)
    p=float(binomtest(wins,wins+losses,.5,alternative='greater').pvalue) if wins+losses else 1.
    improvement=(wins-losses)/len(weak)
    strong=[r for r in records if r['kind']=='pair' and r['strength']==3]
    strong_rate=sum(r['split_target'] for r in strong)/len(strong)
    checks={'null_upper95_le_012':upper<=.12,'weak_gain_at_least_005':improvement>=.05,
            'paired_one_sided_p_le_005':p<=.05,'pair3_power_at_least_half':strong_rate>=.5}
    return {'passed':all(checks.values()),'checks':checks,'null_hits':n,'null_n':len(controls),
            'null_one_sided_upper95':upper,'weak_n':len(weak),'split_only':wins,'full_only':losses,
            'weak_gain':improvement,'paired_one_sided_p':p,'pair3_split_rate':strong_rate}

def summarize(records):
    cells=[]
    for kind,strength in dict.fromkeys((r['kind'],r['strength']) for r in records):
        rs=[r for r in records if (r['kind'],r['strength'])==(kind,strength)]
        c={'kind':kind,'strength':strength,'n':len(rs)}
        for key in ('screen_target','split_any','split_target','full_any','full_target'):
            if kind=='null' and 'target' in key:continue
            k=sum(r[key] for r in rs);c[key]={'count':k,'rate':k/len(rs),'ci95_pointwise':prior.exact_interval(k,len(rs))}
        cells.append(c)
    return cells

def protocol():
    return {'n':1245,'split':[800,445],'screen':'top absolute standardized pair and top absolute standardized triple; lexicographic tie; freeze sign',
      'discovery_spokes':199,'validation_spokes':999,'full_spokes':999,'steps_per_path':'20*draw_count',
      'blocks':{'discovery':[200,400,600,800],'validation':[445],'full':[200,400,600,800,1245]},
      'validation':'one-sided in training direction; Bonferroni2; adjusted p<=.05',
      'baseline':'all1245 draws, two-sided maxT across15180; alpha.05; no old three-family Bonferroni penalty',
      'generation':'same singles-preserving pair and singles/pairs-preserving triple as prior study; independent histories and draws',
      'levels':LEVELS,'null_repeats':100,'repeats_per_alternative':30,'jobs':len(jobs()),'seed':SEED,
      'real_gate':'ALL: null one-sided95% upper<=.12; weak pooled exact-target gain>=.05; paired exact binomial one-sided p<=.05; pair3 target power>=.5',
      'weak_pool':sorted([list(x) for x in WEAK]),'gate_scope':'operational feasibility rule, not a universal optimality claim; no changing rule on failure',
      'actual_if_pass':'historical rounds1..800 screen,801..1245 confirm; freeze candidates before validation; retrospective because this history was examined in other studies',
      'resources':'single CPU BelowNormal; >=50% rest;384MiB peak/1800s per invocation guard; checkpoint; exclusive OS lock',
      'sha256':{f:digest((ROOT/f).read_bytes()) for f in SOURCES}}

class Exclusive:
    def __enter__(self):
        OUT.mkdir(parents=True,exist_ok=True);self.file=(OUT/'run.lock').open('a+b');self.file.seek(0)
        if os.name=='nt':
            import msvcrt
            if not self.file.read(1):self.file.write(b'0');self.file.flush()
            self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return self
    def __exit__(self,*args):self.file.close()

def run():
    prior.old.resources.constrain()
    with Exclusive():
        spec=protocol()
        if (OUT/'protocol.json').exists():assert read('protocol.json')==spec,'frozen protocol mismatch'
        else:save('protocol.json',spec)
        if (OUT/'results.json').exists() and read('results.json')['status']=='completed':
            print('already completed; preserving resource records');return
        records=read('records.json') if (OUT/'records.json').exists() else []
        start=time.monotonic();cpu=time.process_time();initial=len(records);native=Native()
        def pause(tick):
            assert time.monotonic()-start<1800,'wall guard; resume checkpoint'
            assert prior.old.resources.peak_mib()<384,'memory guard'
            time.sleep(max(.001,.5*(time.monotonic()-tick)))
        for index in range(initial,len(jobs())):
            records.append(evaluate(index,native,pause));save('records.json',records)
            save('progress.json',{'state':'running','completed':len(records),'target':len(jobs()),'wall_seconds_this_invocation':time.monotonic()-start,'peak_mib':prior.old.resources.peak_mib()})
            if len(records)%20==0:print('completed',len(records),'/',len(jobs()),flush=True)
        result={'status':'completed','records':len(records),'cells':summarize(records),'gate':gate(records),
                'resources_this_invocation':{'records_at_start':initial,'wall_seconds':time.monotonic()-start,
                'cpu_seconds':time.process_time()-cpu,'peak_mib':prior.old.resources.peak_mib()}}
        save('results.json',result);save('progress.json',{'state':'completed','completed':len(records),'target':len(jobs())})
        print(json.dumps({'gate':result['gate'],'resources':result['resources_this_invocation']}),flush=True)

if __name__=='__main__':run()
