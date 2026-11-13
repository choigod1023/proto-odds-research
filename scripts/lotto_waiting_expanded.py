"""256 actual streams, frozen quarter-selection, exact joint null, Bezout demo."""
import lotto_waiting_study as old
import numpy as np
import json,math,time,hashlib
from pathlib import Path
OUT=old.ROOT/'data/lotto/waiting-expanded'
SEEDS=256;TAKE=64;SEED_START=202610171000

def save(name,value):
 p=OUT/name;tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(p)

def statistic(w):
 order=np.argsort(w[:,0],kind='stable');a,b=order[:TAKE],order[TAKE:]
 return np.array([np.log(w[a,sl]).mean()-np.log(w[b,sl]).mean() for sl in (slice(1,11),slice(11,21),slice(1,21))]),a,b

def egcd(a,b):
 if b==0:return a,1,0
 g,x,y=egcd(b,a%b)
 return g,y,x-(a//b)*y

def bezout_demo(target,pause):
 N=old.SPACE;a=1000003
 while math.gcd(a,N)!=1:a+=2
 g,x,y=egcd(a,N);assert g==1 and a*x+N*y==1
 b=int(np.random.default_rng(202610172888).integers(0,N))
 predicted=(x*(target-b))%N+1
 seen=np.zeros(N,dtype=bool);first=None
 for start in range(0,N,100000):
  t=time.monotonic();indices=np.arange(start,min(start+100000,N),dtype=np.int64)
  ranks=(a*indices+b)%N;seen[ranks]=True
  match=np.flatnonzero(ranks==target)
  if len(match):assert first is None;first=start+int(match[0])+1
  pause(t)
 assert seen.all() and first==predicted
 return {'a':a,'b':b,'modulus':N,'bezout_x':x,'bezout_y':y,'identity':a*x+N*y,
  'actual_enumerated':N,'unique':int(seen.sum()),'target_first_hit':first,'inverse_prediction':predicted,
  'mean_without_replacement':(N+1)/2,'mean_with_replacement':N,
  'note':'target known only for inverse check; no predictive advantage claimed'}

def main():
 old.resources.constrain();started=time.monotonic();cpu=time.process_time()
 rows=[json.loads(l) for name in ('base.jsonl','extra.jsonl') for l in (OUT/name).read_text().splitlines()]
 d={r['draw_no']:r for r in rows};rounds=[1245]+list(range(1235,1245))+list(range(1225,1235))
 targets=[old.rank_combo(d[i]['numbers_sorted']) for i in rounds];assert len(set(targets))==21
 protocol={'seeds':SEEDS,'selected':TAKE,'seed_start':SEED_START,'rounds':rounds,'target_ranks':targets,
  'primary':'log geometric wait ratio selected64 vs remaining192 on old10 targets; lower tail',
  'secondary':'additional10 and all20 descriptive, no new untouched real-data claim',
  'null_repeats':9999,'null':'exact joint first-arrival distribution, reselect64 each replicate',
  'limits':'one CPU BelowNormal, 100000 ranks/chunk, >=50% rest, 384MiB guard, 20minute budget',
  'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
  'inputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*.json*') if p.name in ('base.jsonl','extra.jsonl','prior-32.json')}}
 save('protocol.json',protocol)
 def pause(t):
  assert time.monotonic()-started<1200,'wall budget exceeded'
  assert old.resources.peak_mib()<384,'memory guard exceeded'
  time.sleep(max(.001,.5*(time.monotonic()-t)))
 prior=json.loads((OUT/'prior-32.json').read_text());records=[]
 for i in range(SEEDS):
  seed=SEED_START+i;hits,generated=old.first_hits(seed,targets,pause=pause)
  if i<32:assert hits[:11].tolist()==prior[i]['first_hits'] and seed==prior[i]['seed']
  records.append({'seed':seed,'hits':hits.tolist(),'generated':generated});save('records.json',records)
  if (i+1)%16==0:print('completed',i+1,'/',SEEDS,flush=True)
 w=np.array([r['hits'] for r in records]);actual,a,b=statistic(w)
 rng=np.random.default_rng(202610172999);null=[]
 for start in range(0,9999,20):
  t=time.monotonic()
  null.extend(statistic(v)[0] for v in old.joint_null(rng,min(20,9999-start),seeds=SEEDS,targets=21))
  pause(t)
 null=np.array(null);p=(1+(null<=actual).sum(0))/10000
 math_result=bezout_demo(targets[0],pause)
 result={'status':'completed','seed_count':SEEDS,'selected_count':TAKE,'original32_exact':True,
 'p_values':dict(zip(['primary_old10','secondary_added10','descriptive_all20'],p.tolist())),
 'geometric_ratios':dict(zip(['old10','added10','all20'],np.exp(actual).tolist())),
 'selected_seeds':[int(SEED_START+i) for i in a],'selected_training_waits':w[a,0].tolist(),
 'fastest_seed':int(SEED_START+w[:,0].argmin()),'fastest_training_wait':int(w[:,0].min()),
 'training_mean_all':float(w[:,0].mean()),
 'arithmetic_means':{name:{'selected':float(w[a,sl].mean()),'other':float(w[b,sl].mean())} for name,sl in [('old10',slice(1,11)),('added10',slice(11,21)),('all20',slice(1,21))]},
 'generated_ranks_actual':sum(r['generated'] for r in records),'targets_per_seed':21,
 'bezout':math_result,'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,'peak_mib':old.resources.peak_mib()}
 np.savez_compressed(OUT/'null.npz',log_ratios=null);save('results.json',result)
 print(json.dumps({k:v for k,v in result.items() if k not in ('selected_seeds','selected_training_waits')}),flush=True)

if __name__=='__main__':main()
