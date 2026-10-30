import runpy,contextlib,io,copy,json,gzip,pathlib,hashlib,collections,datetime
with contextlib.redirect_stdout(io.StringIO()):z=runpy.run_path('.local-test/TIPSTER/final_review_audit.py')
e=z['e'];raw=z['raw'];o=z['o'];summary=z['summary'];expected_hash='92df250c9bb7e16f8ad187d0ad7a8ef2afb572816e7699e432bdd2389fd83ec5';assert summary['engine_hash']==expected_hash
actual,audit=e.claimed_time_frames(raw)
# Independently derive source-time filter; no model fitting or result-score calculation.
expect={};cnt=collections.Counter()
for source in raw:
 accepted=[]
 for row in source['raw_forecasts']:
  v=row.get('source_reported_published_at')
  try:
   if not isinstance(v,str) or not v:raise ValueError()
   t=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
   if t.tzinfo is None:raise ValueError()
  except (ValueError,TypeError):cnt['unknown']+=1;continue
  kickoff=datetime.datetime.fromisoformat(source['kickoff'])
  if t>kickoff-datetime.timedelta(minutes=30):cnt['late_for_T30']+=1;continue
  accepted.append((t,row))
 if not accepted:cnt['no_votes_events']+=1;continue
 expect[source['event_id']]=(max(datetime.datetime.fromisoformat(source['available_at']),max(t for t,r in accepted)),[r for t,r in accepted])
assert len(expect)==len(actual)==552
for r in actual:
 t,rows=expect[r['event_id']];assert e.dt(r['available_at'])==t and r['raw_forecasts']==rows and r['availability_proven'] is False
# Explicit missing/None/naive/date-only/invalid, T-30 equality, and late-by-one-second probes.
base=copy.deepcopy(raw[0]);base['kickoff']='2026-01-01T12:00:00+00:00';base['available_at']='2025-12-31T12:00:00+00:00'
for value in [None,'','2026-01-01','2026-01-01T10:00:00','broken',17]:
 base['raw_forecasts']=[{'source_reported_published_at':value}];rr,aa=e.claimed_time_frames([base]);assert not rr and aa['unknown_claimed_time_forecasts_excluded']==1
base['raw_forecasts']=[{}];assert not e.claimed_time_frames([base])[0]
base['raw_forecasts']=[{'source_reported_published_at':'2026-01-01T11:30:00+00:00'}];rr,aa=e.claimed_time_frames([base]);assert len(rr)==1 and e.dt(rr[0]['available_at'])==e.dt('2026-01-01T11:30:00+00:00')
base['available_at']='2026-01-01T11:35:00+00:00';rr,aa=e.claimed_time_frames([base]);assert e.dt(rr[0]['available_at'])==e.dt(base['available_at'])
base['raw_forecasts']=[{'source_reported_published_at':'2026-01-01T11:30:01+00:00'}];assert not e.claimed_time_frames([base])[0]
cp=pathlib.Path('.local-test/CLAIMED/results');cm=json.loads((cp/'manifest.json').read_text(encoding='utf8'));cr=[json.loads(x) for x in gzip.open(cp/'forecast-rosters.jsonl.gz','rt',encoding='utf8')];cn=[json.loads(x) for x in gzip.open(cp/'normalized-frames.jsonl.gz','rt',encoding='utf8')];cs=[json.loads(x) for x in gzip.open(cp/'ticket-sessions.jsonl.gz','rt',encoding='utf8')]
assert cm['code_sha256']==expected_hash and cm['claimed_time_sensitivity'] is True
assert cm['claimed_time_audit']==audit
assert len(cr)==552 and len(cs)==29
ai={r['event_id']:r for r in actual};ci={r['product_id']:r for r in cr}
for frame in cn:
 source=ai[frame['event_id']];assert e.dt(frame['available_at'])==e.dt(source['available_at'])
 counts=e.admissible_votes(source,collections.Counter())
 for r in frame['products']:
  vv=[counts[c['label']]/sum(counts.values()) for c in r['choices']];assert r['external_p']==vv
for session in cs:
 for name,entry in session['policies'].items():
  if name.startswith('external_raw_direction_reference'):assert name.endswith('|forced2')
  if name.startswith('operating_unique_vote_market_reference'):
   for leg in entry['legs']:
    r=ci[leg['product_id']];v=r['external_p'];assert v.count(max(v))==1;k=v.index(max(v));assert leg['choice_id']==r['choices'][k]['choice_id'] and leg['probability']==r['market_p'][k]
summary['source_time_sensitivity']={'code_sha256':expected_hash,'audit':audit,'independent_filter_counts':dict(cnt),'independent_matched_frames':len(actual),'saved_claimed_rosters':len(cr),'saved_claimed_sessions':len(cs),'missing_None_naive_date_invalid_cases_pass':True,'T30_equality_accept_and_plus1second_reject':True,'available_max_original_and_latest_claim_pass':True,'saved_source_vectors_and_operating_legs_pass':True,'rawvote_forced2_only_no_EV':True,'receipt_or_execution_proven':False}
(o/'final-review-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(summary['source_time_sensitivity'],indent=2));print('Primary and claimed audit refreshed; no fits or ROI search.')
