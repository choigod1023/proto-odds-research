import json,gzip,pathlib,collections,hashlib,sys,importlib.util,datetime
sys.dont_write_bytecode=True
root=pathlib.Path('.');o=root/'.local-test/TIPSTER';p=root/'.local-test/RESULTS';m=json.loads((p/'manifest.json').read_text(encoding='utf8'));src=pathlib.Path(next(x['path'] for x in m['inputs'] if '/VOTES/' in x['path']));raw=[json.loads(x) for x in gzip.open(src,'rt',encoding='utf8')];rs=[json.loads(x) for x in gzip.open(p/'forecast-rosters.jsonl.gz','rt',encoding='utf8')];idx={r['event_id']:r for r in raw}
def aware(v):
 try:
  d=datetime.datetime.fromisoformat(v.replace('Z','+00:00'));return d if d.tzinfo else None
 except:return None
summary={'engine_hash':hashlib.sha256((root/'scripts/two_leg_literature_research.py').read_bytes()).hexdigest(),'manifest_hash':m['code_sha256'],'v1_preserved':(root/'.local-test/RESULTS-V1/manifest.json').exists(),'all_rosters':len(rs),'vote_rosters':sum(r['tipster'] for r in rs),'checks':collections.Counter(),'cohort':{},'source_surviving_rows':collections.Counter(),'excluded_late':[]}
for x in raw:
 groups=collections.defaultdict(list)
 for f in x['raw_forecasts']:
  t=aware(f.get('source_reported_published_at'))
  if t and t>=aware(x['kickoff']):summary['excluded_late'].append({'event_id':x['event_id'],'source':f['source'],'author':f['author']});continue
  summary['source_surviving_rows'][f['source']]+=1
  groups[f.get('document_sha256') or f.get('source_sha256') or str((f.get('source'),f.get('author'),f.get('source_url')))].append(f)
 counts=collections.Counter()
 for g in groups.values():
  ss={z.get('side') for z in g}
  if len(ss)==1 and next(iter(ss)) in ('home','away','draw'):counts[next(iter(ss))]+=1
 actual=next((r for r in rs if r['tipster'] and r['event_id']==x['event_id']),None)
 if not counts:summary['checks']['no_surviving_vote_events']+=1;assert actual is None;continue
 assert actual is not None
 assert actual['voters']==sum(counts.values())
 expected=[counts[z['label']]/sum(counts.values()) for z in actual['choices']]
 assert all(abs(a-b)<1e-12 for a,b in zip(expected,actual['external_p']))
 summary['checks']['independently_matched_reconstructed_vectors']+=1
 cc=summary['cohort'].setdefault(x['cohort'],collections.Counter());cc['events']+=1
 v=actual['external_p'];unique=v.count(max(v))==1;cc['unique']+=unique;cc['tie']+=not unique
 if unique:
  k=v.index(max(v));mix=[.9*a+.1*b for a,b in zip(actual['market_p'],v)];cc['unique_vs_fixed90_direction_diff']+=k!=max(range(len(mix)),key=mix.__getitem__)
# Check saved model vectors, selected tickets, common-sample counts without profit fields.
held=[json.loads(x) for x in gzip.open(p/'heldout-predictions.jsonl.gz','rt',encoding='utf8')];ri={r['product_id']:r for r in rs};sessions=[json.loads(x) for x in gzip.open(p/'ticket-sessions.jsonl.gz','rt',encoding='utf8')]
for h in held:
 if h['model']=='operating_unique_vote_market_reference':
  r=ri[h['product_id']];assert r['tipster'] and h['p']==r['market_p'];summary['checks']['operating_prediction_market_vectors']+=1
for s in sessions:
 for name,entry in s['policies'].items():
  if name.startswith('external_raw_direction_reference') and s['cohort'].startswith('retrospective'):assert name.endswith('|forced2')
  if not name.startswith('operating_unique_vote_market_reference'):continue
  for leg in entry['legs']:
   r=ri[leg['product_id']];v=r['external_p'];assert v.count(max(v))==1;k=v.index(max(v));assert leg['choice_id']==r['choices'][k]['choice_id'];assert leg['probability']==r['market_p'][k];summary['checks']['operating_ticket_legs_direction_and_probability']+=1
records=json.loads((p/'results.json').read_text(encoding='utf8'))
for rec in records:
 if rec['model']=='operating_unique_vote_market_reference':
  c=rec['common_unique_vote_forecast'];assert c['markets']==rec['forecast']['markets']==rec['paired_forecast_vs_fixed90']['markets'];summary['checks']['operating_common_and_paired_count_matches']+=1
summary['sessions']=len(sessions)
# Probe dedup key aliases, without fitting or modifying code.
spec=importlib.util.spec_from_file_location('tipster_readonly_engine',root/'scripts/two_leg_literature_research.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
synthetic={'kickoff':'2026-01-01T12:00:00+00:00','raw_forecasts':[{'source':'same','author':a,'source_url':u,'source_sha256':'identical','side':'home'} for a,u in [('a','u1'),('b','u2')]]}
summary['synthetic_source_sha256_alias_vote_count']=sum(e.admissible_votes(synthetic,collections.Counter()).values())
assert summary['synthetic_source_sha256_alias_vote_count']==1
# Synthetic conflicting document is discarded.
synthetic['raw_forecasts'][1]['side']='away'
assert sum(e.admissible_votes(synthetic,collections.Counter()).values())==0
summary['checks']['synthetic_hash_alias_conflict_excluded']=1
# Unique plurality must control selection while p remains the market vector.
base={'tipster':True,'market_p':[.7,.3],'external_p':[0.,1.],'target':1,'event_id':'probe','product_id':'probe'}
metric=e.forecast_metrics([base],'operating_unique_vote_market_reference',{},{});assert metric['accuracy_pct']==100
assert e.predict(base,'operating_unique_vote_market_reference',{}, {})==[.7,.3]
base['external_p']=[.5,.5]
assert e.forecast_metrics([base],'operating_unique_vote_market_reference',{}, {})['markets']==0
summary['checks']['synthetic_operating_tie_and_market_probability']=1
assert m['strict_asof_training_certified'] is False and m['result_receipt_times_verified'] is False
summary['checks']['strict_asof_unverified_explicit']=1
# Recompute support hashes from saved predictions; no score or profit recomputation.
for rec in records:
 selected=[h for h in held if h['cohort']==rec['cohort'] and h['split']==rec['split'] and h['model']==rec['model'] and (rec['league']=='ALL' or h['league']==rec['league']) and h['target'] is not None]
 for field in ['forecast','common_unique_vote_forecast']:
  metric=rec.get(field)
  if not metric or 'support_sha256' not in metric:continue
  rr=[ri[h['product_id']] for h in selected]
  if field=='common_unique_vote_forecast' or rec['model']=='operating_unique_vote_market_reference':rr=[r for r in rr if r['external_p'].count(max(r['external_p']))==1]
  digest=hashlib.sha256('\n'.join(sorted(r['product_id'] for r in rr)).encode()).hexdigest()
  assert digest==metric['support_sha256'], (rec['model'],field)
  summary['checks']['support_hash_independent_matches']+=1
summary['raw_source_sha256_rows']=sum(bool(f.get('source_sha256')) for x in raw for f in x['raw_forecasts'])
assert all(r['final_p'] is not None for r in rs if not r['tipster'])
summary['checks']['timed_complete_final_rosters']=sum(not r['tipster'] for r in rs)
fits=json.loads((p/'fits.json').read_text(encoding='utf8'))
for f in fits:
 assert f['strict_asof_training_certified'] is False and f['result_receipt_times_verified'] is False
 for kind in ['train','recent']:
  z=f[kind]
  for side,field in [('train','train_ids'),('validation','validation_ids')]:
   count=len({ri[k]['event_id'] for k in z[field]})
   assert count==z[side+'_events']
   summary['checks']['fit_event_count_metadata_matches']+=1
summary['training_gate_and_shrinkage_implementation']='Reviewed: global and league minimum counts use unique event_id; league shrinkage uses unique validation/train event counts. No fitting rerun.'
summary['code_unchanged_since_manifest']=summary['engine_hash']==summary['manifest_hash'];assert summary['code_unchanged_since_manifest']
(o/'final-review-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(summary,ensure_ascii=False,indent=2))
