"""Exact-margin, reversible hub-and-spoke conditional association tests.

The finite-path Besag-Clifford construction makes the observed matrix and
spoke endpoints exchangeable under the conditional null; mixing affects
power, not test validity. All spokes run sequentially on one CPU.
"""
import lotto_coverage_resources as resources
import itertools
import json
import hashlib
import math
import random
import time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/lotto/combination-dependence'
PAIRS=list(itertools.combinations(range(45),2))
TRIPLES=list(itertools.combinations(range(45),3))
FEATURES=PAIRS+TRIPLES
PLOOK=np.full((45,45),-1,dtype=np.int16)
TLOOK=np.full((45,45,45),-1,dtype=np.int16)
for i,(a,b) in enumerate(PAIRS):PLOOK[a,b]=i
for i,(a,b,c) in enumerate(TRIPLES):TLOOK[a,b,c]=i
PCOLS=np.array(list(itertools.combinations(range(6),2)))
TCOLS=np.array(list(itertools.combinations(range(6),3)))

def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/name;tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    tmp.replace(path)

def counts(rows):
    a=np.array([sorted(r) for r in rows],dtype=np.int16)
    p=a[:,PCOLS];t=a[:,TCOLS]
    return np.concatenate((np.bincount(PLOOK[p[:,:,0],p[:,:,1]].ravel(),minlength=990),
        np.bincount(TLOOK[t[:,:,0],t[:,:,1],t[:,:,2]].ravel(),minlength=14190))).astype(np.int16)

def margins(rows,block):
    return np.array([np.bincount([v for row in rows[s:s+block] for v in row],minlength=45)
                     for s in range(0,len(rows),block)])

def trade(rows,i,j,rng):
    common=rows[i]&rows[j];a=rows[i]-common;b=rows[j]-common
    pool=sorted(a|b)
    take=set(rng.sample(pool,len(a)))
    rows[i]=common|take;rows[j]=common|(set(pool)-take)

def walk(rows,steps,block,rng,pause=None):
    assert len(rows)%block==0 and block>=2
    for start in range(0,steps,1000):
        tick=time.monotonic()
        for _ in range(min(1000,steps-start)):
            base=block*rng.randrange(len(rows)//block)
            i=rng.randrange(block);j=rng.randrange(block-1)
            if j>=i:j+=1
            trade(rows,base+i,base+j,rng)
        if pause:pause(tick)

def sample(rows,replicates=999,steps=20000,block=200,seed=202610173101,pause=None,progress=None):
    rng=random.Random(seed);original=[set(r) for r in rows]
    target=margins(original,block);hub=[set(r) for r in original]
    walk(hub,steps,block,rng,pause)
    out=np.empty((replicates+1,15180),dtype=np.int16);out[0]=counts(original)
    distances=[]
    for s in range(replicates):
        endpoint=[set(r) for r in hub]
        walk(endpoint,steps,block,rng,pause)
        assert all(len(r)==6 for r in endpoint)
        assert np.array_equal(margins(endpoint,block),target)
        out[s+1]=counts(endpoint)
        distances.append(sum(len(a&b) for a,b in zip(endpoint,original))/(6*len(original)))
        if progress and (s+1)%25==0:progress(s+1)
    return out,{'spoke_count':replicates,'steps_each':steps,'block_size':block,
                'same_position_incidence_fraction_mean':float(np.mean(distances)),
                'same_position_incidence_fraction_range':[float(min(distances)),float(max(distances))],
                'all_margins_preserved':True,'seed':seed}

def analyze(matrix):
    # All centering/scaling uses every row, including observed, symmetrically.
    # This preserves exchangeability even with finite-length Markov paths.
    means=matrix.mean(axis=0);variances=matrix.var(axis=0)
    sd=np.sqrt(np.maximum(variances,.25));max_abs=np.zeros(len(matrix))
    for start in range(0,matrix.shape[1],256):
        z=np.abs((matrix[:,start:start+256]-means[start:start+256])/sd[start:start+256])
        max_abs=np.maximum(max_abs,z.max(axis=1))
    observed_z=(matrix[0]-means)/sd
    local_p=np.array([np.mean(max_abs>=abs(z)) for z in observed_z])
    local_adjusted=np.minimum(1,3*local_p)
    dispersion=[np.square(matrix[:,sl].astype(np.int64)).sum(axis=1) for sl in (slice(0,990),slice(990,None))]
    statistics=dispersion+[max_abs]
    raw=[float(np.mean(s>=s[0])) for s in statistics]
    names=['pair_dispersion','triple_dispersion','max_standardized_pair_or_triple']
    selected=np.flatnonzero(local_adjusted<=.05).tolist()
    top=sorted(range(15180),key=lambda j:(-abs(observed_z[j]),j))[:12]
    def detail(j):
        return {'numbers':[v+1 for v in FEATURES[j]],'observed_count':int(matrix[0,j]),
                'exchangeable_center':float(means[j]),'standardized_residual':float(observed_z[j]),
                'family_adjusted_p':float(local_adjusted[j]),'index':j}
    return {'global_tests':{n:{'raw_p':p,'adjusted_p':min(1,3*p),'observed_statistic':float(s[0])}
                           for n,p,s in zip(names,raw,statistics)},
            'selected_count':len(selected),'selected':[detail(j) for j in selected],
            'top_descriptive_only':[detail(j) for j in top]},selected

def validation(selected,matrix,holdout,pause):
    """Fixed significant interactions -> five tickets; exact coverage baseline.

    Weights use training data only. Score = sum selected signed shrunk effects.
    Main endpoint: test-round frequency of >=3 hits in at least one frozen
    ticket vs its exact uniform-draw coverage. >=4 and 6 are descriptive.
    """
    from scipy.stats import binomtest
    means=matrix.mean(0);var=matrix.var(0)
    weights=np.zeros(15180)
    weights[selected]=np.clip((matrix[0,selected]-means[selected])/(var[selected]+4),-2,2)
    plook=np.zeros((45,45));tlook=np.zeros((45,45,45))
    for j in selected:
        if j<990:plook[FEATURES[j]]=weights[j]
        else:tlook[FEATURES[j]]=weights[j]
    stream=itertools.combinations(range(45),6);best=[]
    while True:
        tick=time.monotonic();rows=list(itertools.islice(stream,25000))
        if not rows:break
        a=np.array(rows);p=a[:,PCOLS];t=a[:,TCOLS]
        score=plook[p[:,:,0],p[:,:,1]].sum(1)+tlook[t[:,:,0],t[:,:,1],t[:,:,2]].sum(1)
        # stable ordering retains lexicographic tie-breaking
        ii=np.argsort(-score,kind='stable')[:5]
        best=sorted(best+[(float(score[i]),tuple(rows[i])) for i in ii],key=lambda x:(-x[0],x[1]))[:5]
        pause(tick)
    tickets=[[v+1 for v in row] for _,row in best]
    save('frozen_rule.json',{'selected_indices':selected,'weights':weights[selected].tolist(),'tickets':tickets})
    coverage=resources.count({'fixed':tickets},pause=lambda t,n:pause(t))['fixed']
    max_hits=[max(len(set(draw)&set(t)) for t in tickets) for draw in holdout]
    N=math.comb(45,6)
    results={str(t):{'successes':sum(x>=t for x in max_hits),'rounds':len(max_hits),
                    'uniform_probability':coverage['coverage_counts'][str(t)]/N} for t in (3,4,6)}
    q=results['3']['uniform_probability'];k=results['3']['successes']
    return {'status':'evaluated','tickets':tickets,'thresholds':results,
            'primary_p_greater':float(binomtest(k,len(max_hits),q,alternative='greater').pvalue),
            'interpretation':'retrospective later rounds, previously seen in other project experiments'}

def main():
    resources.constrain();start=time.monotonic();cpu=time.process_time();OUT.mkdir(parents=True,exist_ok=True)
    rows=[json.loads(s) for name in ('base.jsonl','extra.jsonl') for s in (OUT/name).read_text().splitlines()]
    assert [r['draw_no'] for r in rows]==list(range(1,1246))
    train=[{v-1 for v in r['numbers_sorted']} for r in rows[:1000]]
    protocol={'train_rounds':[1,1000],'validation_rounds':[1001,1245],'block_size':200,
              'null':'uniform binary matrices with row sum6 and exact per-block observed column sums',
              'null_model':'independent weighted six-subsets with log-additive single-number weights constant within each block; no pair/triple effects',
              'sampler':'reversible Curveball; Besag-Clifford hub-and-spoke; 20000 steps hub and each of999 spokes; single CPU',
              'features':{'pairs':990,'triples':14190},'tests':['pair sum squared counts','triple sum squared counts','maximum absolute standardized count'],
              'multiplicity':'maxT across15180 features; Bonferroni3 across two dispersion tests and maxT family',
              'gate':'any local family_adjusted_p<=0.05; otherwise validation not opened',
              'validation':'freeze significant signed interaction weights (count-center)/(variance+4), clipped[-2,2]; enumerate top5; test later >=3-hit frequency against exact uniform coverage; one-sided binomial',
              'power_control':'one strong synthetic clustered example, n200, seed202610173102, 99 spokes x4000 steps; not a power curve',
              'main_seed':202610173101,'resources':'one CPU BelowNormal, >=50% rest, 384MiB/1200s guards',
              'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'data_sha256':{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ('base.jsonl','extra.jsonl')}}
    save('protocol.json',protocol)
    def pause(tick):
        assert time.monotonic()-start<1200,'wall guard'
        assert resources.peak_mib()<384,'memory guard'
        time.sleep(max(.001,.5*(time.monotonic()-tick)))
    def progress(n):
        save('progress.json',{'state':'sampling','completed':n,'target':999,'wall_seconds':time.monotonic()-start})
        print('spokes',n,'/999',flush=True)
    matrix,diag=sample(train,pause=pause,progress=progress)
    np.savez_compressed(OUT/'conditional_counts.npz',counts=matrix)
    analysis,selected=analyze(matrix)
    # Independent positive control: equal expected marginal frequencies but
    # strong co-occurrence inside five disjoint groups of nine labels.
    rng=random.Random(202610173102)
    control=[set(rng.sample(list(range(9*(i%5),9*(i%5)+9)),6)) for i in range(200)]
    cm,cd=sample(control,replicates=99,steps=4000,block=200,seed=202610173103,pause=pause)
    ca,_=analyze(cm);np.savez_compressed(OUT/'positive_control_counts.npz',counts=cm)
    if selected:
        heldout=validation(selected,matrix,[r['numbers_sorted'] for r in rows[1000:]],pause)
    else:
        heldout={'status':'not_triggered','reason':'no multiplicity-corrected local association; no fitted rule promoted','rounds':[1001,1245]}
    result={'status':'verified','analysis':analysis,'sampler_diagnostics':diag,
            'positive_control':{'global_tests':ca['global_tests'],'selected_count':ca['selected_count'],'diagnostics':cd},
            'validation':heldout,'wall_seconds':time.monotonic()-start,'cpu_seconds':time.process_time()-cpu,
            'peak_mib':resources.peak_mib()}
    save('results.json',result);save('progress.json',{'state':'verified','completed':999,'target':999})
    print(json.dumps({k:v for k,v in result.items() if k!='analysis'}),flush=True)
    print(json.dumps(analysis['global_tests']),flush=True)

if __name__=='__main__':main()
