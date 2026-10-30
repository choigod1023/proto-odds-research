import pathlib,json,gzip,collections,hashlib
p=pathlib.Path('.local-test/RESULTS');o=pathlib.Path('.local-test/TIPSTER')
def load(x):return [json.loads(t) for t in gzip.open(x,'rt',encoding='utf8')]
manifest=json.loads((p/'manifest.json').read_text()); raw=load(pathlib.Path(next(x['path'] for x in manifest['inputs'] if '/VOTES/' in x['path'])));rs=load(p/'forecast-rosters.jsonl.gz');fits=json.loads((p/'fits.json').read_text()); held=load(p/'heldout-predictions.jsonl.gz'); idx={r['event_id']:r for r in raw}
def stat(rows):
 c=collections.Counter()
 for r in rows:
  c['rosters']+=1;c['target_known']+=r['target'] is not None
  v=r['external_p'];m=r['market_p'];leaders=[i for i,x in enumerate(v) if abs(x-max(v))<1e-12];mix=[.9*a+.1*b for a,b in zip(m,v)];mixed=max(range(len(mix)),key=mix.__getitem__);market=max(range(len(m)),key=m.__getitem__)
  if len(leaders)!=1:c['tied_vote']+=1;continue
  k=leaders[0];c['unique_vote']+=1;c['unique_vote_target_known']+=r['target'] is not None;c['unique_vote_vs_9to1_direction_diff']+=k!=mixed;c['unique_vote_vs_market_direction_diff']+=k!=market
  c['raw_vote_share_differs_market_at_selected']+=abs(v[k]-m[k])>1e-12
  c['selected_market_prob_at_least_55pct']+=m[k]>=.55
 return dict(c)
summary={'engine_sha256':hashlib.sha256(pathlib.Path('scripts/two_leg_literature_research.py').read_bytes()).hexdigest(),'manifest_code_sha256':manifest['code_sha256'],'counts_no_fitting_no_roi':True,'all_votes':stat([r for r in rs if r['tipster']]),'by_cohort':{},'test_slices':[],'aliases':[]}
for co in sorted({r['cohort'] for r in rs if r['tipster']}):summary['by_cohort'][co]=stat([r for r in rs if r['cohort']==co])
for f in fits:
 if f['cohort'].startswith('retrospective'):
  subset=[r for r in rs if r['cohort']==f['cohort'] and r['day'] in f['test_days']];summary['test_slices'].append({'cohort':f['cohort'],'split':f['split'],**stat(subset)})
for r in rs:
 if not r['tipster']:continue
 x=idx[r['event_id']]
 if x['round_ambiguous_multiple_saved_offers']:summary['aliases'].append({'event_id':x['event_id'],'saved_issue_rounds':x['saved_issue_rounds'],'selected_issue':r['issue'],'selected_game_no':r['game_no']})
summary['alias_events']=len(summary['aliases']);summary['raw_source_ties']=sum(r['source_plurality_tie'] for r in raw)
summary['actual_vote_reference_supported_events_without_timing_proof']=summary['all_votes']['unique_vote'];summary['formally_operating_eligible_events']=sum(r['availability_proven'] for r in raw)
summary['reference_model_labels_in_saved_predictions']=dict(collections.Counter(r['model'] for r in held if r['cohort'].startswith('retrospective')))
(o/'review-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in summary.items() if k!='aliases'},ensure_ascii=False,indent=2))
