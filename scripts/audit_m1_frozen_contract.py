"""Offline audit of frozen KBO M1; no training, network, or service changes.

Dense equations adapted from the source project's independent_verify.py, not its
training or prediction implementation. All external paths must be supplied.
"""
import argparse, collections, gzip, hashlib, json, math
from decimal import Decimal
from pathlib import Path
W=[x/4.3 for x in (4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9)]

def gz(path):
 rows=[json.loads(z) for z in gzip.decompress(path.read_bytes()).splitlines()]
 out={x['fixture_id']:x for x in rows}
 if len(out)!=len(rows): raise ValueError('duplicate fixture')
 return out

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def scalar(name,value):
 if value is None or name=='park_factor':return None
 if name.endswith('_rest_days'):return float(value) if type(value) is int and value>=0 else None
 if name.endswith('_team_strength'):
  a=[x['runs_for']-x['runs_against'] for x in value if all(type(x.get(k)) is int and x[k]>=0 for k in ('runs_for','runs_against'))];return sum(a)/len(a) if a else None
 if name.endswith('_lineup_strength'):
  obs=[x for p in value for x in p['history'] or [] if all(type(x.get(k)) is int and x[k]>=0 for k in ('ab','hit','bb')) and x['hit']<=x['ab']];den=sum(x['ab']+x['bb'] for x in obs);return sum(x['hit']+x['bb'] for x in obs)/den if den else None
 if name.endswith('_starter_strength'):
  obs=[x for x in value['appearances'] if all(type(x.get(k)) is int and x[k]>=0 for k in ('er','outs'))];outs=sum(x['outs'] for x in obs);return 27*sum(x['er'] for x in obs)/outs if outs else None
 games=value['games'];return float(sum(x['relief']['outs'] for x in games)) if games and all(type(x['relief']['outs']) is int and x['relief']['outs']>=0 for x in games) else None

def vector(row,src,side,spec):
 other='away' if side=='home' else 'home';keys=(side+'_team_strength',other+'_team_strength',side+'_rest_days',other+'_rest_days','park_factor',side+'_lineup_strength',other+'_starter_strength',other+'_bullpen_load');raw=[scalar(k,src['features'][k]['value']) for k in keys];missing=[float(x is None) for x in raw];norm=[(x-spec['mean'][i])/spec['scale'][i] if x is not None else 0. for i,x in enumerate(raw)];return norm,missing

def roster(src,scenario,side):
 if scenario=='projected':return src['projection'][side+'_lineup']['player_ids'],src['projection'][side+'_starter']['player_id']
 return [p['player_id'] for p in src['official_lineup_context']['lineups'][side]],src['official_schedule_pitcher_context']['pitchers'][side]['player_id']

def manual(row,src,scenario,model):
 spec=model['spec'];team=spec['teams'];hitters=spec['hitters'];starters=spec['starters'];hm={k:i for i,k in enumerate(hitters)};sm={k:i for i,k in enumerate(starters)};d=model['nbase']+(len(hitters)+len(starters) if model['family']!='M0' else 0);beta=model['beta'];rol={s:roster(src,scenario,s) for s in ('home','away')};num={s:vector(row,src,s,spec) for s in rol};flags={}
 for s,o in (('home','away'),('away','home')):
  h=rol[s][0];p=rol[o][1];flags[s]=[float(h is None),sum(x not in hm for x in h)/9 if h else 0.,float(p is None),float(p is not None and p not in sm)]
 if model['family'] in ('M0','M1'):
  rates=[]
  for s,o in (('home','away'),('away','home')):
   base=[1.,float(s=='home')]+[float(row[s+'_id']==x) for x in team]+[float(row[o+'_id']==x) for x in team]+num[s][0]+num[s][1]
   if model['family']=='M1':base+=flags[s]
   z=sum(a*b for a,b in zip(base,beta))
   if model['family']=='M1':
    for j,p in enumerate(rol[s][0] or []):
     if p in hm:z+=W[j]*beta[len(base)+hm[p]]
    p=rol[o][1]
    if p in sm:z+=beta[len(base)+len(hitters)+sm[p]]
   rates.append(math.exp(z))
  h,a=rates;ph=[math.exp(-h)];pa=[math.exp(-a)]
  for j in range(1,100):ph.append(ph[-1]*h/j);pa.append(pa[-1]*a/j)
  p=[0.,0.,0.]
  for i,hi in enumerate(ph):
   for j,aj in enumerate(pa):p[0 if i>j else 1 if i==j else 2]+=hi*aj
  return p,rates
 base=[1.]+[float(row['home_id']==t)-float(row['away_id']==t) for t in team]+[a-b for a,b in zip(num['home'][0],num['away'][0])]+[a-b for a,b in zip(num['home'][1],num['away'][1])]+[a-b for a,b in zip(flags['home'],flags['away'])];logits=[]
 for k in range(2):
  coeff=beta[k*d:(k+1)*d];z=sum(a*b for a,b in zip(base,coeff))
  for side,sign in [('home',1),('away',-1)]:
   for j,p in enumerate(rol[side][0] or []):
    if p in hm:z+=sign*W[j]*coeff[len(base)+hm[p]]
   p=rol[side][1]
   if p in sm:z+=sign*coeff[len(base)+len(hitters)+sm[p]]
  logits.append(z)
 logits.append(0.);peak=max(logits);v=[math.exp(x-peak) for x in logits];return [x/sum(v) for x in v],None


KEY='kbo-player-effects-v2-2026-M1'
M0='kbo-player-effects-v2-2026-M0'
TIER='extended_day_team_source_confirmed'

def stats(items):
 net=sum((Decimal(x['net']) for x in items),Decimal(0)); n=len(items)
 return {'n':n,'hits':sum(x['hit'] for x in items),'voids':sum(x['void'] for x in items), 'net':str(net),'roi':float(net/n) if n else None,'m1_m0_different':sum(x['different'] for x in items)}

def audit(root, matched):
 inputs=root/'inputs'; artifacts=root/'artifacts'
 files=[root/'research.py',root/'original_adapter.py',root/'contract.json',artifacts/'final-models.json',artifacts/'predictions.json',artifacts/'results.json',inputs/'features.jsonl.gz',inputs/'fresh-history-2026-official_batting_and_starter.jsonl.gz',inputs/'fresh-history-2026-projected.jsonl.gz',inputs/'test-labels.jsonl.gz',matched]
 hashes={str(p.name):sha(p) for p in files}
 expected={'contract.json':'6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed','final-models.json':'61bbcc11459ad7308bd0c881ac16cd54f4c1b5ecabab7745bfc78e78ec1e17be','predictions.json':'ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9','results.json':'211b89d365136a166fd13026df5f6358e4b7ecda5db50b4459a2f42e75f19cd8','test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','rows.json':'34250a17437b3cfd6a19c915a092c19bfa1356ddf823b0bc537e9e6d88c43882'}
 for name, digest in expected.items():
  if hashes[name]!=digest: raise ValueError('frozen hash mismatch: '+name)
 manifest=json.loads((inputs/'manifest.json').read_bytes())
 for p in (inputs/'features.jsonl.gz',inputs/'fresh-history-2026-official_batting_and_starter.jsonl.gz',inputs/'fresh-history-2026-projected.jsonl.gz'):
  if sha(p)!=manifest['files'][p.name]: raise ValueError('source hash mismatch: '+p.name)
 if sha(root/'contract.json')!=manifest['contract_sha256']: raise ValueError('contract hash mismatch')
 frozen=json.loads((artifacts/'final-models.json').read_bytes()); models=frozen['models']; saved=json.loads((artifacts/'predictions.json').read_bytes()); results=json.loads((artifacts/'results.json').read_bytes()); matched_rows=json.loads(matched.read_bytes())
 if len(matched_rows)!=103 or len({x['fixture_id'] for x in matched_rows})!=103: raise ValueError('103 fixture cohort mismatch')
 subset={x['fixture_id']:x for x in matched_rows}; base=gz(inputs/'features.jsonl.gz'); labels=gz(inputs/'test-labels.jsonl.gz'); output={}; maxerr=0.; maxmatched=0.
 for scenario in ('projected','official_batting_and_starter'):
  src=gz(inputs/('fresh-history-2026-'+scenario+'.jsonl.gz')); predictions={x['fixture_id']:x for x in saved['scenarios'][scenario]['M1']}; ledger=results['price_ledgers'][scenario][TIER]; audited=[]
  if len(ledger)!=667 or len({x['fixture_id'] for x in ledger})!=667: raise ValueError('667 cohort mismatch')
  for entry in ledger:
   fid=entry['fixture_id']; p,_=manual(base[fid],src[fid],scenario,models['M1']); q=p[0]/(p[0]+p[2]); stored=predictions[fid]; maxerr=max(maxerr,*[abs(p[i]-stored['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))]); pick='home' if p[0]>p[2] else 'away' if p[2]>p[0] else None
   if pick is None: raise ValueError('tied model selection')
   price=Decimal(str(entry['price_source']['prices'][pick])); y=labels[fid]; winner='home' if y['home_score']>y['away_score'] else 'away' if y['away_score']>y['home_score'] else 'draw'
   if winner!=entry['winner']: raise ValueError('winner mismatch')
   net=Decimal(0) if winner=='draw' else price-1 if winner==pick else Decimal(-1)
   if entry['forecasts']['M1']['pick']!=pick or Decimal(entry['forecasts']['M1']['net'])!=net: raise ValueError('saved settlement mismatch')
   p0,_=manual(base[fid],src[fid],scenario,models['M0']); pick0='home' if p0[0]>p0[2] else 'away' if p0[2]>p0[0] else None
   item={'fixture_id':fid,'date':entry['date'],'pick':pick,'price':str(price),'home_probability':q,'winner':winner,'net':str(net),'hit':pick==winner,'void':winner=='draw','different':pick!=pick0}
   if fid in subset and scenario=='official_batting_and_starter':
    x=subset[fid]; maxmatched=max(maxmatched,abs(q-x['model_home_probabilities'][KEY]));
    if x['prices']!=entry['price_source']['prices'] or x['winner']!=winner or x['selections'][KEY]!=pick or abs(float(x['profit'][KEY])-float(net))>1e-9: raise ValueError('matched price, outcome or pick mismatch: '+fid)
   audited.append(item)
  if not set(subset).issubset({x['fixture_id'] for x in audited}): raise ValueError('103 not subset of 667')
  groups={'all':audited,'matched_103':[x for x in audited if x['fixture_id'] in subset], 'complement':[x for x in audited if x['fixture_id'] not in subset]}
  output[scenario]={name:{'overall':stats(xs),'monthly':{month:stats([x for x in xs if x['date'].startswith(month)]) for month in sorted({x['date'][:7] for x in xs})},'below_1_20':stats([x for x in xs if Decimal(x['price'])<Decimal('1.20')]),'at_or_below_1_20':stats([x for x in xs if Decimal(x['price'])<=Decimal('1.20')])} for name,xs in groups.items()}
  output[scenario]['rows']=audited
 if maxerr>1e-9 or maxmatched>1e-9: raise ValueError('probability mismatch')
 return {'hashes_sha256':hashes,'selected_family':frozen['selected_family'],'training_n':frozen['training_n'],'max_3way_error':maxerr,'max_matched_conditional_error':maxmatched,'scenarios':output,'historical_receipt_proven':False,'strict_eligible_matched':sum(bool(x['strict_eligible']) for x in matched_rows)}

def main():
 parser=argparse.ArgumentParser(); parser.add_argument('--source',type=Path,required=True);parser.add_argument('--matched',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 result=audit(args.source,args.matched);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8');print(json.dumps({k:v for k,v in result.items() if k!='scenarios'}));print(json.dumps({s:{g:x['overall'] for g,x in v.items() if g!='rows'} for s,v in result['scenarios'].items()},ensure_ascii=False))
if __name__=='__main__':main()

