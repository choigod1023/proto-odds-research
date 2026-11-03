import json,subprocess,hashlib,math
from pathlib import Path
here=Path(__file__).parent
commit='01650a673d6a6f65afc1ae9f3127319a88cc266e'
b=subprocess.check_output(['git','-C',r'C:\Users\user\Documents\Codex\kbmnext','show',commit+':experiments/kbo-m0-followup-20261103/results.json'])
assert hashlib.sha256(b).hexdigest()=='f81adcdcab0c2a55977b42dd69bc779afdd4ec4c3123e6495c714cb141ca47b8'
target=json.loads(b); own=json.loads((here/'stability-audit-result.json').read_text(encoding='utf-8-sig'))['반기별결과']
def eq(a,b,where):
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):
  if not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12):raise ValueError((where,a,b))
 elif a!=b:raise ValueError((where,a,b))
models={'baseline':'baseline','recency':'shrink','distribution':'nb03','equal_ensemble':'equal_ensemble'}
groups=[]
for y in ('2024','2025','2026'):
 groups.append((y+' annual',own[y]['전체'],target['annual'][y]))
 for h,n in [('1','상반기'),('2','하반기')]:groups.append((y+' H'+h,own[y][n],target['halves'][y][h]))
groups.append(('2026 strict',own['2026']['가격민감도']['strict_original_time_match'],target['2026_strict_price_sensitivity']))
for title,a,b in groups:
 for x,y in [('전체경기','target_n'),('가격경기','price_n'),('가격없는경기','missing_price_n'),('무승부','target_draws'),('가격없는무승부','missing_draws')]:eq(a[x],b['coverage'][y],title+y)
 for x,y in models.items():
  o=a['모델'][x]; out=b['outcome'][y]; price=b['price'][y]
  for k in ('n','hits','accuracy','logloss','brier','decisive','decisive_hits'):eq(o['전체결과'][k],out[k],title+y+k)
  for k in ('accuracy','logloss','brier'):eq(o['전체결과']['conditional_'+k],out['decisive_'+k],title+y+k)
  for k,l in [('가격적중','hits'),('환급','refunds'),('net','net_units'),('roi','roi'),('월별','monthly_units'),('일마감낙폭','max_daily_close_drawdown_units')]:eq(o[k],price[l],title+y+l)
 pair=a['paired_기준대비']['equal_ensemble'];other=b['price_paired_ensemble_minus_baseline']
 for x,y in [('weeks','weeks'),('changed','changed_picks'),('accuracy_ci95','decisive_accuracy_delta_ci95'),('roi_ci95','roi_delta_ci95')]:
  aa,bb=pair[x],other[y]
  if isinstance(aa,list):
   for i,(u,v) in enumerate(zip(aa,bb)):eq(u,v,title+y+str(i))
  else:eq(aa,bb,title+y)
print(json.dumps({'study_commit':commit,'results_sha256':'f81adcdcab0c2a55977b42dd69bc779afdd4ec4c3123e6495c714cb141ca47b8','groups':len(groups),'model_comparisons':len(groups)*4,'coverage_outcome_price_and_paired_price_CI':'matched','outcome_paired_CI':'not independently checked'},indent=2))
