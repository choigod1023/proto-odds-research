"""Actually enumerate iid uniform combination ranks; compare first-hit transfer."""
import os
for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'):os.environ[key]='1'
import sys,json,math,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'entropy_support'))
import lotto_nonlinear as resources
import numpy as np
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/lotto/waiting-study'
SPACE=math.comb(45,6);BATCH=100000

def rank_combo(numbers,n=45,k=6):
 numbers=sorted(numbers);assert len(set(numbers))==k and all(1<=x<=n for x in numbers)
 rank=0;previous=0
 for i,value in enumerate(numbers):
  rank+=sum(math.comb(n-v,k-i-1) for v in range(previous+1,value));previous=value
 return rank

def unrank(rank,n=45,k=6):
 assert 0<=rank<math.comb(n,k);result=[];previous=0
 for i in range(k):
  for value in range(previous+1,n+1):
   count=math.comb(n-value,k-i-1) if n-value>=k-i-1 else 0
   if rank<count:result.append(value);previous=value;break
   rank-=count
 return result

def first_hits(seed,targets,space=SPACE,batch=BATCH,pause=None):
 rng=np.random.default_rng(seed);hit=np.zeros(len(targets),np.int64);offset=0
 assert len(set(targets))==len(targets)
 while not np.all(hit):
  t=time.monotonic();draws=rng.integers(0,space,size=batch,dtype=np.int64)
  for j in np.flatnonzero(hit==0):
   matches=np.flatnonzero(draws==targets[j])
   if len(matches):hit[j]=offset+int(matches[0])+1
  offset+=batch
  if pause:pause(t)
 return hit,offset

def statistic(waits):
 """Log ratio of geometric mean test waiting: selected8 / other24."""
 order=np.argsort(waits[:,0],kind='stable');selected=order[:8];other=order[8:]
 value=float(np.log(waits[selected,1:]).mean()-np.log(waits[other,1:]).mean())
 return value,selected,other

def joint_null(rng,repeats,seeds=32,targets=11,space=SPACE):
 """Exact iid categorical first-arrival law, preserving target dependence.
 When m targets remain unseen, next new-target gap is geometric(m/space).
 New target identities occur in a uniform random order, independently of gaps.
 """
 order=np.argsort(rng.random((repeats,seeds,targets)),axis=2)
 gaps=rng.geometric(np.arange(targets,0,-1)/space,size=(repeats,seeds,targets))
 arrival=np.cumsum(gaps,axis=2);out=np.empty_like(arrival)
 np.put_along_axis(out,order,arrival,axis=2)
 return out

def save(name,data):
 path=OUT/name;tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(path)

def main():
 resources.constrain();started=time.monotonic();cpu=time.process_time()
 rows=[json.loads(l) for name in ('base.jsonl','extra.jsonl') for l in (OUT/name).read_text().splitlines()]
 byround={r['draw_no']:r for r in rows};rounds=[1245]+list(range(1235,1245));targets=[rank_combo(byround[i]['numbers_sorted']) for i in rounds]
 assert len(set(targets))==11
 protocol={'seed_start':202610171000,'seeds':32,'selected':8,'rounds':rounds,'target_ranks':targets,'space':SPACE,
 'generator':'numpy default_rng PCG64, uniform integers with replacement; bijective lexicographic combination ranks',
 'selection':'eight smallest waiting times for 1245; tie seed index',
 'primary':'log geometric-mean waiting ratio on 10 other targets, selected8 vs remaining24, lower is better',
 'null':'9999 exact iid joint first-arrival simulations; repeat full selection each time',
 'resource':'one CPU BelowNormal; chunks100000; sleep >=50% compute per chunk; 384MiB guard; 600 seconds budget',
 'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'data_hashes':{name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in ('base.jsonl','extra.jsonl')}}
 save('protocol.json',protocol);records=[]
 def pause(t):
  assert time.monotonic()-started<600,'wall budget exceeded; completed seeds saved'
  assert resources.peak_mib()<384,'working-set guard exceeded'
  time.sleep(max(.001,.5*(time.monotonic()-t)))
 for i in range(32):
  seed=protocol['seed_start']+i;hits,generated=first_hits(seed,targets,pause=pause)
  records.append({'seed':seed,'first_hits':hits.tolist(),'ranks_generated':generated})
  save('records.json',records);print('seed complete',i+1,'training hit',int(hits[0]),flush=True)
 waits=np.array([x['first_hits'] for x in records]);observed,selected,other=statistic(waits)
 rng=np.random.default_rng(202610171999);null=[]
 for start in range(0,9999,100):
  t=time.monotonic();block=joint_null(rng,min(100,9999-start))
  null.extend(statistic(w)[0] for w in block);pause(t)
 null=np.array(null);p=(1+int(np.sum(null<=observed)))/10000
 result={'status':'completed','primary_p_lower':p,'observed_log_ratio':observed,'geometric_wait_ratio':math.exp(observed),
 'selected_seeds':[records[i]['seed'] for i in selected],
 'selected_training_waits':waits[selected,0].tolist(),
 'selected_test_arithmetic_mean':float(waits[selected,1:].mean()),'other_test_arithmetic_mean':float(waits[other,1:].mean()),
 'selected_test_geometric_mean':float(np.exp(np.log(waits[selected,1:]).mean())),
 'other_test_geometric_mean':float(np.exp(np.log(waits[other,1:]).mean())),
 'all_training_mean':float(waits[:,0].mean()),'all_training_min':int(waits[:,0].min()),
 'total_generated_ranks':sum(r['ranks_generated'] for r in records),'unique_seeds':32,'targets_per_seed':11,
 'null_ratio_interval95':np.exp(np.quantile(null,[.025,.975])).tolist(),
 'test_rounds_selected_faster':int(np.sum(waits[selected,1:].mean(0)<waits[other,1:].mean(0))),
 'per_test_round':[{'draw':rounds[j],'selected_mean':float(waits[selected,j].mean()),'other_mean':float(waits[other,j].mean())} for j in range(1,11)],
 'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,'peak_working_set_mib':resources.peak_mib()}
 np.savez_compressed(OUT/'null_statistics.npz',log_ratios=null)
 save('results.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
