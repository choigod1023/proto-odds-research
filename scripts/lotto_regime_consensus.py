"""Exploratory equal-family rank consensus, not estimated winning odds."""
import lotto_entropy_regime as r
import lotto_nonlinear as n
import lotto_entropy_study as e
import json,time
import numpy as np
from scipy.stats import rankdata

def main():
 n.constrain();start=time.monotonic()
 rows=[json.loads(line) for name in ('base.jsonl','extra.jsonl') for line in (r.OUT/name).read_text().splitlines()]
 d=np.array([row['numbers_sorted'] for row in rows]);x=np.zeros((len(d),45),bool);x[np.arange(len(d))[:,None],d-1]=True
 rng=np.random.default_rng(2026101707)
 c=np.unique(np.sort(np.argpartition(rng.random((100000,45)),5,axis=1)[:,:6]+1,axis=1),axis=0)
 cat=r.labels(c);traincat=r.labels(d[:740])
 q=np.array(json.loads((r.OUT/'quantum-actual.json').read_text())['category_probabilities'])
 classical=(np.bincount(traincat,minlength=64)+740*r.BASE)/1480
 counts=np.zeros(45);mass=0.
 for row in x:counts=2**(-1/50)*counts+row;mass=2**(-1/50)*mass+1
 prob=(counts+4500*6/45)/(mass+4500);weights=prob/(1-prob)
 rank=lambda v:(rankdata(v,method='average')-.5)/len(v)
 families=[rank(np.log(q[cat]/r.BASE[cat])),rank(np.log(classical[cat]/r.BASE[cat])),rank(np.log(weights[c-1]).sum(1))]
 _,ep=e.score(x);overlap=np.isin(c,d[-1]).sum(1)
 a=ep['theta']*overlap-np.log((e.BASE*np.exp(ep['theta']*e.K)).sum())
 p=ep['conditional_ball1'][int(x[-1,0])];b=ep['iid_ball1'];has1=(c==1).any(1)
 families.append((rank(a)+rank(np.where(has1,np.log(p/b),np.log((1-p)/(1-b)))))/2)
 f=n.features(d);_,pars=n.scores(f,True);future=np.vstack([f,np.zeros((1,4),int)]);cf=n.features(c);votes=[]
 for j in range(4):
  context=n.contexts(future,j)
  for k in range(6):
   p=pars[j*6+k];votes.append(rank(np.log(np.array(p['probabilities'])[context[k][-1],cf[:,j]]/np.array(p['base'])[cf[:,j]])))
 families.append(np.mean(votes,axis=0))
 regimes=json.loads((r.OUT/'results.json').read_text())['parameters']['next_probabilities']
 families.append(np.mean([rank(np.log(np.array(p)[cat]/r.BASE[cat])) for p in regimes],axis=0))
 matrix=np.column_stack(families);order=np.lexsort((-matrix.mean(1),-matrix.min(1)))[:5]
 result={'seed':2026101707,'candidates':len(c),'data_through_draw':1245,'target_draw':1246,
 'family_order':['quantum','classical_category','shrinkage_50_4500','temporal','nonlinear','entropy_regime'],
 'criterion':'maximize weakest family percentile, then mean; within-family average ranks; ties are midranks',
 'global_optimum':False,'validated_predictive_advantage':False,
 'games':[{'numbers':c[i].tolist(),'family_percentiles':(100*matrix[i]).tolist(),'mean_percentile':float(100*matrix[i].mean()),'minimum_percentile':float(100*matrix[i].min())} for i in order],
 'wall_seconds':time.monotonic()-start,'peak_memory_mib':n.peak_mib()}
 assert len(set(tuple(g['numbers']) for g in result['games']))==5
 r.save('consensus.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':main()
