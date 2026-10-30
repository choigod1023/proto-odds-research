import gzip,json,pathlib,collections,itertools,datetime,hashlib
src=pathlib.Path(r'C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/single-vs-low-odds-parlays-20261030/experiments/single-parlay-20261030/inputs/VOTES/events.jsonl.gz')
out=pathlib.Path('.local-test/TIPSTER'); rows=[json.loads(x) for x in gzip.open(src,'rt',encoding='utf8')]
def dt(v):
 try:
  t=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
  return t if t.tzinfo else None
 except:return None
sources=collections.Counter();authors=collections.Counter();pairs={}; urls=collections.defaultdict(list);hashes=collections.defaultdict(list);times=collections.Counter();duplicates=[];voters=collections.Counter();cohorts=collections.Counter();encoding=0
for r in rows:
 cohorts[r['cohort']]+=1; voters[r['voters']]+=1; kick=dt(r['kickoff']); identities=collections.defaultdict(set)
 for f in r['raw_forecasts']:
  s=f.get('source');a=f.get('author');sources[s]+=1;authors[(s,a)]+=1;identities[(s,a)].add(f.get('side'))
  encoding+=bool(a and '\ufffd' in a)
  urls[f.get('source_url')].append((r['event_id'],s,a,f.get('side')))
  h=f.get('document_sha256') or f.get('source_sha256')
  if h:hashes[h].append((r['event_id'],s,a,f.get('side')))
  for field,label in [('source_reported_published_at','published'),('observed_at','observed')]:
   t=dt(f.get(field));times[label+'_parseable']+=bool(t);times[label+'_before_kickoff']+=bool(t and kick and t<kick);times[label+'_at_or_after_kickoff']+=bool(t and kick and t>=kick)
  times['cutoff_proven']+=bool(f.get('cutoff_proven'))
 if len(r['raw_forecasts'])!=len(identities):duplicates.append(r['event_id'])
 for (a,sa),(b,sb) in itertools.combinations(sorted(identities.items()),2):
  k=(a,b);v=pairs.setdefault(k,{'overlap':0,'agree':0,'ambiguous':0});v['overlap']+=1
  if len(sa)==len(sb)==1:v['agree']+=sa==sb
  else:v['ambiguous']+=1
report={'scope':'VOTES provenance only; no fitting, scoring or ROI','input':str(src),'input_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'events':len(rows),'unique_event_ids':len({r['event_id'] for r in rows}),'cohorts':dict(cohorts),'league_events':dict(collections.Counter(r['league'] for r in rows)),'raw_forecasts':sum(sources.values()),'source_rows':dict(sources),'author_rows':[{'source':s,'author':a,'rows':n} for (s,a),n in authors.most_common()],'unique_source_author_labels':len(authors),'author_rows_with_unicode_replacement_character':encoding,'voters_histogram':dict(voters),'multi_author_events':sum(r['voters']>1 for r in rows),'ties':sum(r['source_plurality_tie'] for r in rows),'duplicate_source_author_within_event':duplicates,'excluded_conflicts_events':sum(bool(r['conflicting_source_authors_excluded']) for r in rows),'timestamps':dict(times),'availability_proven_events':sum(r['availability_proven'] for r in rows),'quote_observed_verified_events':sum(r['quote_observed_at_verified'] for r in rows),'author_pairs':[{'a':a,'b':b,**v,'agreement_fraction':v['agree']/v['overlap']} for (a,b),v in sorted(pairs.items(),key=lambda x:-x[1]['overlap'])],'reused_urls_groups':sum(len(v)>1 for k,v in urls.items() if k),'reused_hash_groups':sum(len(v)>1 for k,v in hashes.items() if k),'cross_author_same_document_hash_groups':[{'sha256':k,'rows':v} for k,v in hashes.items() if len({(x[1],x[2]) for x in v})>1],'limitations':['Source-author labels are not verified unique human identities.','Shared URL/hash across events can be a multi-game roundup, not copying.','Pair agreement can reflect common market favorites; no causal copying inference.','No text bodies in export: semantic copying, revision histories, and independent authorship untestable.','Availability timestamps are hypothetical; publication claims do not prove pregame capture.']}
report['timestamps']['published_missing']=sum(not f.get('source_reported_published_at') for r in rows for f in r['raw_forecasts'])
report['timestamps']['published_present_without_aware_timestamp']=sum(bool(f.get('source_reported_published_at')) and dt(f.get('source_reported_published_at')) is None for r in rows for f in r['raw_forecasts'])
report['publication_at_or_after_kickoff_examples']=[{'event_id':r['event_id'],'kickoff':r['kickoff'],'source':f['source'],'author':f['author'],'published':f['source_reported_published_at'],'url':f['source_url']} for r in rows for f in r['raw_forecasts'] if dt(f.get('source_reported_published_at')) and dt(r['kickoff']) and dt(f['source_reported_published_at'])>=dt(r['kickoff'])]
manifest=json.loads((src.parent/'manifest.json').read_text(encoding='utf8'))
report['manifest_sha256_matches']=report['input_sha256']==manifest['sha256']
assert report['manifest_sha256_matches']
assert report['events']==597 and report['unique_event_ids']==597
assert sum(voters.values())==597 and sum(sources.values())==635
(out/'feature-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in report.items() if k not in ['author_rows','cross_author_same_document_hash_groups','limitations','author_pairs']},ensure_ascii=False,indent=2));print('PAIRS',json.dumps(report['author_pairs'],ensure_ascii=False));print('HASH_CROSS_AUTHOR',len(report['cross_author_same_document_hash_groups']))
