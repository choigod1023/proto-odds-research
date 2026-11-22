"""Fixed PR639 model transfer: offline, no outcome-based training or selection."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'; os.environ['OMP_NUM_THREADS']='1'; os.environ['MKL_NUM_THREADS']='1'
import argparse, gzip, json, hashlib, re, sys
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal
import numpy as np
import frozen_reference as f
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from accuracy_formula_lab import load_unique_market_rows
from matches import load_matches
LEAGUES=('K리그1','K리그2','EPL','라리가'); FAMILIES=('승무패','핸디캡','언더오버')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--raw',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 raw=Path(a.raw); sha=hashlib.sha256(raw.read_bytes()).hexdigest()
 if sha!=f.EXPECTED:raise ValueError('input hash mismatch')
 records,quality=load_unique_market_rows(raw); matches=load_matches(sports=('sc',),path=raw)
 scores={(r.kickoff,r.league,r.home_team,r.away_team):(int(r.home_score),int(r.away_score)) for r in matches.itertuples(index=False)}
 grouped=defaultdict(lambda:defaultdict(list)); exclusions=Counter(); inventory=Counter()
 for r in records:
  t,s,l,h,aw,fam,booking,label,n=r['key']
  if l not in LEAGUES or s!='sc' or not (2025<=t.year<=2026) or fam not in FAMILIES:continue
  inventory[l+'/'+fam]+=1
  if (fam=='승무패' and n!=3) or (fam=='핸디캡' and n!=3) or (fam=='언더오버' and n!=2):exclusions['unsupported_nway']+=1;continue
  line=None
  if fam!='승무패':
   m=re.fullmatch(r'[HU]\s+([+-]?\d+(?:\.\d+)?)',label)
   if not m:exclusions['bad_line']+=1;continue
   line=float(m.group(1))
   if (fam=='핸디캡' and line%1) or (fam=='언더오버' and line%1!=.5):exclusions['unsupported_line']+=1;continue
  key=(t,l,h,aw);score=scores.get(key)
  if score is None:exclusions['missing_score']+=1;continue
  if f.choose_outcome(fam,line,*score)!=r['winner']:exclusions['result_conflict']+=1;continue
  grouped[key][fam].append({'family':fam,'line':line,'odds':r['odds'],'outcome':r['winner'],'booking':booking})
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True); rows=[]; numerical=[]; counts=Counter()
 for key,groups in sorted(grouped.items()):
  if any(len(groups[x])!=1 for x in FAMILIES):counts['missing_or_ambiguous/'+key[1]]+=1;continue
  offers=[groups[x][0] for x in FAMILIES]; mt=[(f.masks(o['family'],o['line']),f.devig(o['odds'])) for o in offers]
  q1=f.fit(mt[:1]); qa=f.fit(mt); qd=f.fit(mt,True); qp=f.project(q1,mt)
  distributions={'poisson_1x2':q1,'poisson_all':qa,'dc_all':qd,'kl_soft':qp}
  for name,q in distributions.items():
   if not np.isfinite(q).all() or min(q)<0 or abs(sum(q)-1)>1e-10:raise ValueError('grid invalid')
   for o,(m,tgt) in zip(offers,mt):
    prob=m@q;pick=int(np.argmax(prob));y=o['outcome'];net=Decimal(str(o['odds'][pick]))-1 if pick==y else Decimal(-1)
    rows.append({'event_id':'|'.join(map(str,key)),'day':str(key[0])[:10],'sport':'soccer','league':key[1],'home':key[2],'away':key[3],'score':scores[key], 'family':o['family'],'line':o['line'],'booking':o['booking'],'odds':o['odds'],'market_p':tgt.tolist(),'y':y,'model':name,'p':prob.tolist(),'pick':pick,'hit':pick==y,'net':str(net)})
  for o,(_,tgt) in zip(offers,mt):
   pick=int(np.argmax(tgt));y=o['outcome'];net=Decimal(str(o['odds'][pick]))-1 if pick==y else Decimal(-1)
   rows.append({'event_id':'|'.join(map(str,key)),'day':str(key[0])[:10],'sport':'soccer','league':key[1],'home':key[2],'away':key[3],'score':scores[key], 'family':o['family'],'line':o['line'],'booking':o['booking'],'odds':o['odds'],'market_p':tgt.tolist(),'y':y,'model':'market_devig','p':tgt.tolist(),'pick':pick,'hit':pick==y,'net':str(net)})
  if key[1].startswith('K리그'):
   edge=float(qp.reshape(11,11)[-1,:].sum()+qp.reshape(11,11)[:-1,-1].sum())
   try:
    f.H,f.A=np.indices((16,16));f.H=f.H.ravel();f.A=f.A.ravel()
    mt15=[(f.masks(o['family'],o['line']),f.devig(o['odds'])) for o in offers];q15=f.project(f.fit(mt15[:1]),mt15)
    numerical.append({'event_id':'|'.join(map(str,key)),'edge_mass_10':edge,'tail_mass_above_10_grid15':float(q15.reshape(16,16)[11:,:].sum()+q15.reshape(16,16)[:11,11:].sum()),'market_probability_max_abs_diff':float(max(np.max(np.abs(m@q15-m10@qp)) for (m,_),(m10,_) in zip(mt15,mt)))})
   finally:
    f.H,f.A=np.indices((11,11));f.H=f.H.ravel();f.A=f.A.ravel()
  counts['matches/'+key[1]]+=1
 with gzip.open(out/'predictions.jsonl.gz','wt',encoding='utf8',newline='\n',compresslevel=9) as z:
  for r in rows:z.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
 (out/'inventory.json').write_text(json.dumps({'sha256':sha,'loader_quality':quality,'selected_leagues':LEAGUES,'period':'2025-01-01 through 2026-12-31 as available','market_inventory':dict(inventory),'counts':dict(counts),'exclusions':dict(exclusions),'grid15_numerical_only':numerical},ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
