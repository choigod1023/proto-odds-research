import sys,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import lotto_waiting_expanded as m
import numpy as np

def test_bezout_identity_and_affine_bijection():
 for N in (12,35,100):
  for a in range(1,N):
   g,x,y=m.egcd(a,N);assert a*x+N*y==g==math.gcd(a,N)
   if g==1:
    ranks=[(a*i+7)%N for i in range(N)]
    assert len(set(ranks))==N
    for target in range(N):assert ranks[(x*(target-7))%N]==target

def test_selection_uses_training_only():
 w=np.arange(1,256*21+1).reshape(256,21);before=m.statistic(w)[1]
 w[:,1:]=w[::-1,1:]
 assert np.array_equal(before,m.statistic(w)[1]) and len(before)==64

def test_split_statistics_match_manual():
 w=np.random.default_rng(4).integers(1,10000,(256,21));v,a,b=m.statistic(w)
 assert np.isclose(v[0],np.log(w[a,1:11]).mean()-np.log(w[b,1:11]).mean())
 assert np.isclose(v[2],(v[0]+v[1])/2)
