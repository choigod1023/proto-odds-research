"""Export frozen KBO predictions and compare original research models, without refitting."""
import gzip,hashlib,json,re
from pathlib import Path
R=Path('C:/Users/user/Documents/Codex');OUT=Path(__file__).resolve().parent
A=R/'kbtmodel/experiments/kbo-actual-training-20261103'; B=R/'kbeffects/experiments/kbo-player-effects-20261103'
def raw(p):return p.read_bytes()
def h(b):return hashlib.sha256(b).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def obj(p):return json.loads(raw(p))
def lines(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(s) for s in f]
def idx(rows):
 out={}
 for x in rows:
  k=x['fixture_id']
  if k in out:raise ValueError('duplicate ID '+k)
  out[k]=x
 return out
rows=idx(lines(B/'inputs/rows.jsonl.gz')); dev=obj(A/'artifacts/development.json'); final=obj(A/'artifacts/predictions.json'); coefficients=obj(A/'artifacts/final-models.json')['models']; effect=obj(B/'artifacts/predictions.json'); ecoef=obj(B/'artifacts/final-models.json')['models']
ledger_a=idx(obj(A/'artifacts/results.json')['ledgers']['actual']['extended_day_team_source_confirmed']);ledger_b=idx(obj(B/'inputs/price-ledgers.json')['ledgers']['extended_day_team_source_confirmed']); overlap=sorted(set(ledger_a)&set(ledger_b))
ma=idx(final['predictions']['actual']['M0_actual']);old=idx(final['predictions']['actual']['oldfull_M0']);mb=idx(effect['scenarios']['official_batting_and_starter']['M0'])
price_diff=sum(ledger_a[i]['price_source']!=ledger_b[i]['price_source'] for i in overlap);winner_diff=sum(ledger_a[i]['winner']!=ledger_b[i]['winner'] for i in overlap)
def pick(p):return max(('home','away'),key=lambda k:p['probabilities_2way'][k])
comparison={'n_actual':len(ledger_a),'n_effect_price_ledger':len(ledger_b),'n_common':len(overlap),'price_source_different':price_diff,'winner_different':winner_diff,'probability_pairs_different':sum(ma[i]['probabilities_2way']!=mb[i]['probabilities_2way'] for i in overlap),'selection_different':sum(pick(ma[i])!=pick(mb[i]) for i in overlap),'effects_m0_equals_oldfull_m0':sum(old[i]['probabilities_2way']==mb[i]['probabilities_2way'] for i in overlap),'net_difference_actual_minus_effects':round(sum(float(ledger_a[i]['forecasts']['M0_actual']['net'])-float(ledger_a[i]['forecasts']['oldfull_M0']['net']) for i in overlap),2),'timing':'both quote receipt times unverified; same price_source object'}
codeA=h(raw(A/'pipeline.py'));codeB=h(raw(B/'original_model.py')); exported=[];missing=[]; manifests=[]
def export(source,models,modeldefs,year,series,commit,train_end,codehash,branch):
 sourcehash=h(raw(source))
 for model,arr in models.items():
  mid=series+'-'+str(year)+'-'+model; modelhash=h(canonical(modeldefs[model])); count=0
  for z in arr:
   fixture=z['fixture_id'];f=rows.get(fixture)
   if not f or not f.get('kickoff_at') or not f.get('home_id') or not f.get('away_id'):
    missing.append({'model_id':mid,'fixture_id':fixture,'reason':'missing_fixture_metadata'});continue
   source_fixture=fixture[:-4] if re.fullmatch(r'\d{8}[A-Z]{4}\d20\d\d',fixture) else None
   payload={'model_id':mid,'model_family':model,'model_coefficient_sha256':modelhash,'model_code_sha256':codehash,'input_branch':branch,'input_record_ids':[],'input_artifact_fixture_id':fixture,'train_end':train_end,'as_of':None,'probabilities':z['probabilities_2way'],'probabilities_3way':z['probabilities_3way'],'selection':pick(z),'applicable_leagues':['KBO'],'source_fixture_id':source_fixture,'fixture_id':fixture,'limitations':['retrospective roster; input receipt times not proven','prediction as_of not established','input_record_ids unresolved; see source artifacts']}
   exported.append({'record_id':'model_prediction:'+h(canonical([mid,fixture]))[:24],'kind':'model_prediction','sport':'bs','league':'KBO','kickoff':f['kickoff_at'],'home':f['home_id'],'away':f['away_id'],'source_url':f'https://github.com/choigod1023/proto-odds-research/blob/{commit}/experiments/'+('kbo-actual-training-20261103/artifacts/' if source.parent==A/'artifacts' else 'kbo-player-effects-20261103/artifacts/')+source.name,'body_sha256':sourcehash,'received_at':None,'timing_basis':'retrospective','payload':payload});count+=1
  manifests.append({'model_id':mid,'coefficient_sha256':modelhash,'code_sha256':codehash,'input_branch':branch,'train_end':train_end,'exported':count,'artifact_rows':len(arr),'source_artifact_sha256':sourcehash})
export(A/'artifacts/development.json',{k:v for k,v in dev['predictions']['actual'].items() if k in ('M0_actual','M2_actual')},dev['models'],2025,'kbo-actual-training','0277fa14b2e7ee2f950d683cf0093f9f1b6d788d','2024-10-28',codeA,'paired-train-actual / 2025 validation subset')
export(A/'artifacts/predictions.json',{k:v for k,v in final['predictions']['actual'].items() if k in ('M0_actual','M2_actual')},coefficients,2026,'kbo-actual-training','ff2bef35988eb5add154b4bebf8b265f42792db1','2025-10-31',codeA,'paired-train-actual / paired-test-actual')
export(B/'artifacts/predictions.json',effect['scenarios']['official_batting_and_starter'],ecoef,2026,'kbo-player-effects-v2','433d915aa52727de4af128cd9e614ca80aa8aa72','2025-10-31',codeB,'projected-training / official-batting-and-starter-evaluation')
encoded=('\n'.join(json.dumps(x,ensure_ascii=False,separators=(',',':')) for x in exported)+'\n').encode();(OUT/'model-predictions.jsonl.gz').write_bytes(gzip.compress(encoded,mtime=0))
result={'comparison':comparison,'models':manifests,'missing_fixture_metadata':missing,'prediction_records':len(exported),'uncompressed_sha256':h(encoded),'actual_training_M1':'notimplemented for both years','limitations':['train_end is latest training game date, not proof of model availability','no pregame receipt proven','fixture metadata from archived source, identity requires mapping; no fuzzy joins','2025 and 2026 KBO artifacts only; other sports and leagues not covered']}
(OUT/'model-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'comparison':comparison,'models':[(x['model_id'],x['exported']) for x in manifests],'missing':len(missing)},ensure_ascii=False))
