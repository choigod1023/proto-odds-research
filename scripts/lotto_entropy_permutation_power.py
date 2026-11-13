import sys,json,time
from pathlib import Path
sys.path.insert(0,'scripts')
import lotto_entropy_study as m
m.constrain();rng=m.np.random.default_rng(2026101702);t=time.monotonic()
z=m.np.load(m.OUT/'scores.npz');mu=z['scale'].mean(0);sd=z['scale'].std(0,ddof=1)
output={}
for kind,strength in [('static',.5),('copy_one',.25)]:
 hits=0
 for i in range(100):
  assert time.monotonic()-t<1800,'Wall budget exceeded'
  start=time.monotonic();d=m.generate(rng,1,kind,strength)[0]
  actual=((m.score(d)[0]-mu)/sd).max()
  null=m.np.array([((m.score(d[rng.permutation(m.N)])[0]-mu)/sd).max() for _ in range(199)])
  hits+=m.tail(null,actual)<=.05
  time.sleep(.25*(time.monotonic()-start))
 output[kind]={'strength':strength,'detections':int(hits),'n':100,'rate':hits/100,'ci95':m.interval(hits,100)}
 print(kind,output[kind],flush=True)
output['wall_seconds']=time.monotonic()-t
m.save('permutation-power.json',output)
