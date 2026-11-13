"""Exact two-pass exhaustive midrank consensus with disk-backed shrinkage scores."""
import lotto_entropy_regime as r
import lotto_nonlinear as n
import lotto_entropy_study as e
import numpy as np
import json,time,math,itertools,heapq,hashlib

OUT=r.ROOT/'data/lotto/full-consensus';TEMP=r.ROOT/'.local-build'
TOTAL=math.comb(45,6);BATCH=25000

def batches(numbers=45,size=6,batch=BATCH):
 it=itertools.combinations(range(1,numbers+1),size)
 while True:
  rows=list(itertools.islice(it,batch))
  if not rows:break
  yield np.array(rows,dtype=np.int16)

def weighted_rank(values,counts):
 """Midrank percentile with empirical multiplicities; exact ties pooled."""
 values=np.asarray(values);counts=np.asarray(counts)
 return np.array([(counts[values<v].sum()+.5*counts[values==v].sum())/counts.sum() for v in values])

def continuous_rank(sorted_values,values):
 return (np.searchsorted(sorted_values,values,side='left')+np.searchsorted(sorted_values,values,side='right'))/(2*len(sorted_values))

def exact_keys(matrix,total):
 # Every family rank is a multiple of 1/(48*total): 24-model midrank
 # averages have the largest denominator. Avoid float noise breaking ties.
 units=np.rint(matrix*(48*total)).astype(np.int64)
 assert np.allclose(units/(48*total),matrix,rtol=0,atol=1e-14)
 return units.min(1),units.sum(1)

def save(name,data):(OUT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def main():
 n.constrain();started=time.monotonic();cpu=time.process_time()
 def pause(t):
  assert time.monotonic()-started<1800,'30 minute wall budget exceeded'
  assert n.peak_mib()<384,'384 MiB process working-set guard exceeded'
  time.sleep(max(.01,.5*(time.monotonic()-t)))
 source=r.ROOT/'scripts/lotto_full_consensus.py'
 save('protocol.json',{'total':TOTAL,'batch':BATCH,'family_weights':'six equal; within-family average model ranks',
  'selection':'maximize minimum family percentile, then average; exact ties use lexicographic numbers',
  'midrank':'(count strictly lower + 0.5 * count equal) / total',
  'resources':'one CPU, BelowNormal, disk-backed float64 scores, 50% rest per batch, 384 MiB peak guard, 30 minute budget',
  'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
  'input_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in r.OUT.glob('*.json*')},
  'data_through':1245,'target_draw':1246,'models_refitted':False})
 rows=[json.loads(line) for name in ('base.jsonl','extra.jsonl') for line in (r.OUT/name).read_text().splitlines()]
 d=np.array([row['numbers_sorted'] for row in rows]);assert len(d)==1245
 x=np.zeros((len(d),45),bool);x[np.arange(len(d))[:,None],d-1]=True
 counts=np.zeros(45);mass=0.
 for row in x:counts=2**(-1/50)*counts+row;mass=2**(-1/50)*mass+1
 p=(counts+4500*6/45)/(mass+4500);logw=np.log(p/(1-p))
 q=np.array(json.loads((r.OUT/'quantum-actual.json').read_text())['category_probabilities'])
 classical=(np.bincount(r.labels(d[:740]),minlength=64)+740*r.BASE)/1480
 _,ep=e.score(x)
 f=n.features(d);_,pars=n.scores(f,True);future=np.vstack([f,np.zeros((1,4),int)])
 regimes=json.loads((r.OUT/'results.json').read_text())['parameters']['next_probabilities']
 raw=np.memmap(TEMP/'shrinkage.raw',dtype='float64',mode='w+',shape=(TOTAL,))
 hist=[np.zeros(k,dtype=np.int64) for k in n.SIZES];overlap_hist=np.zeros(7,dtype=np.int64);cat_hist=np.zeros(64,dtype=np.int64)
 offset=0
 for c in batches():
  t=time.monotonic();end=offset+len(c);raw[offset:end]=logw[c-1].sum(1)
  cf=n.features(c)
  for j,k in enumerate(n.SIZES):hist[j]+=np.bincount(cf[:,j],minlength=k)
  overlap_hist+=np.bincount(np.isin(c,d[-1]).sum(1),minlength=7)
  cat_hist+=np.bincount(r.labels(c),minlength=64);offset=end
  if offset%1000000==0:print('pass1',offset,flush=True)
  pause(t)
 raw.flush();assert offset==TOTAL and np.array_equal(cat_hist,r.COUNTS)
 assert np.array_equal(overlap_hist,np.array([math.comb(6,k)*math.comb(39,6-k) for k in range(7)]))
 ordered=np.memmap(TEMP/'shrinkage.sorted',dtype='float64',mode='w+',shape=(TOTAL,))
 for start in range(0,TOTAL,BATCH):ordered[start:start+BATCH]=raw[start:start+BATCH]
 t=time.monotonic();ordered.sort(kind='quicksort');ordered.flush();pause(t)
 assert all(np.all(ordered[i+1:min(i+BATCH+1,TOTAL)]>=ordered[i:min(i+BATCH,TOTAL-1)]) for i in range(0,TOTAL-1,BATCH))
 # All non-shrinkage scores have finite categorical support; exact multiplicities
 # give the same ranks as ranking an expanded 8,145,060-element score vector.
 def category_rank(prob):
  v=np.zeros(64);v[r.VALID]=np.log(np.array(prob)[r.VALID]/r.BASE[r.VALID])
  return weighted_rank(v,cat_hist)
 qr=category_rank(q);cr=category_rank(classical)
 er=np.mean([category_rank(p) for p in regimes],axis=0)
 ov=ep['theta']*np.arange(7)-np.log((e.BASE*np.exp(ep['theta']*e.K)).sum());ovrank=weighted_rank(ov,overlap_hist)
 prob=ep['conditional_ball1'][int(x[-1,0])];base=ep['iid_ball1']
 brank=weighted_rank(np.array([np.log((1-prob)/(1-base)),np.log(prob/base)]),np.array([math.comb(44,6),math.comb(44,5)]))
 nr=[]
 for j in range(4):
  ctx=n.contexts(future,j);ranks=[]
  for k in range(6):
   p=pars[j*6+k];v=np.log(np.array(p['probabilities'])[ctx[k][-1]]/np.array(p['base']))
   ranks.append(weighted_rank(v,hist[j]))
  nr.append(np.sum(ranks,axis=0)/24)
 heap=[];offset=0
 for c in batches():
  t=time.monotonic();cf=n.features(c);cat=r.labels(c)
  matrix=np.column_stack([qr[cat],cr[cat],continuous_rank(ordered,raw[offset:offset+len(c)]),
    (ovrank[np.isin(c,d[-1]).sum(1)]+brank[(c==1).any(1).astype(int)])/2,
    sum(nr[j][cf[:,j]] for j in range(4)),er[cat]])
  minimum,mean=exact_keys(matrix,TOTAL)
  best=np.lexsort((np.arange(len(c)),-mean,-minimum))[:5]
  for i in best:
   entry=(int(minimum[i]),int(mean[i]),-int(offset+i),c[i].tolist(),matrix[i].tolist())
   if len(heap)<5:heapq.heappush(heap,entry)
   elif entry[:3]>heap[0][:3]:heapq.heapreplace(heap,entry)
  offset+=len(c)
  if offset%1000000==0:print('pass2',offset,flush=True)
  pause(t)
 assert offset==TOTAL
 result={'status':'completed','enumerated_each_pass':TOTAL,'passes':2,'unique_combinations':TOTAL,
  'data_through_draw':1245,'target_draw':1246,
  'families':['quantum','classical','shrinkage','temporal','nonlinear','entropy_regime'],
  'games':[{'numbers':v[3],'minimum_percentile':v[0]*100/(48*TOTAL),'mean_percentile':v[1]*100/(6*48*TOTAL),'family_percentiles':[a*100 for a in v[4]],'lexicographic_index':-v[2]} for v in sorted(heap,reverse=True)],
  'category_counts_match_exact':True,'overlap_counts_match_exact':True,'feature_counts':[h.tolist() for h in hist],
  'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,'peak_working_set_mib':n.peak_mib(),
  'validated_probability_advantage':False}
 save('results.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
