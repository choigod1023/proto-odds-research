import sys,itertools,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import lotto_full_consensus as m
import numpy as np
from scipy.stats import rankdata

def test_enumeration_no_omission_duplicate_or_order_change():
 actual=np.concatenate(list(m.batches(10,6,13)))
 assert actual.tolist()==[list(x) for x in itertools.combinations(range(1,11),6)]
 assert len(actual)==math.comb(10,6)

def test_weighted_ranks_match_expanded_scores_with_ties():
 values=np.array([1.,3.,3.,-1.,100.]);counts=np.array([4,2,5,3,0])
 expanded=np.repeat(values,counts);ranks=(rankdata(expanded)-.5)/len(expanded)
 small=m.weighted_rank(values,counts)
 for v,p,c in zip(values,small,counts):
  if c:assert np.allclose(ranks[expanded==v],p)

def test_continuous_midrank_matches_direct_sort():
 values=np.array([2.,1.,2.,4.,-3.])
 assert np.array_equal(m.continuous_rank(np.sort(values),values),(rankdata(values)-.5)/len(values))

def test_exact_rank_keys_ignore_roundoff_in_mathematical_ties():
 v=np.full((2,6),.5);v[1,0]+=1e-16
 low,total=m.exact_keys(v,100)
 assert low[0]==low[1] and total[0]==total[1]

def test_batch_top5_heap_equals_global_sort():
 rng=np.random.default_rng(9);v=rng.integers(0,5,(200,6))/5
 expected=np.lexsort((np.arange(len(v)),-v.mean(1),-v.min(1)))[:5]
 heap=[]
 for start in range(0,200,17):
  b=v[start:start+17];low=b.min(1);mean=b.mean(1)
  for i in np.lexsort((np.arange(len(b)),-mean,-low))[:5]:
   key=(float(low[i]),float(mean[i]),-int(start+i))
   if len(heap)<5:m.heapq.heappush(heap,key)
   elif key>heap[0]:m.heapq.heapreplace(heap,key)
 assert [-x[2] for x in sorted(heap,reverse=True)]==expected.tolist()

def test_diversity_filter_enforces_pair_overlap_and_usage():
 c=np.array([[1,2,3,7,8,9],[1,2,7,8,9,10],[7,8,9,10,11,12]])
 usage=np.zeros(46,dtype=int);usage[12]=2
 assert m.eligible(c,[[1,2,3,4,5,6]],usage).tolist()==[False,True,False]
