import datetime,gzip,json,collections
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
def read(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]
def parse(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
g=read(P/'stints.jsonl.gz'); priced=read(P/'priced-matches.jsonl.gz');byid={x['id']:x for x in g}
fixtures=[json.loads(x) for x in (ROOT/'experiments/pregame-basketball-20261104/nba-2026-sample.jsonl').read_text(encoding='utf8').splitlines()]; differences=[]
for x in fixtures:
 if x['kind']=='fixture':
  game=byid[x['payload']['source_fixture_id']];differences.append({'id':game['id'],'minutes_after_official_schedule':(parse(game['start'])-parse(x['kickoff'])).total_seconds()/60})
ks=datetime.timezone(datetime.timedelta(hours=9)); gaps=[];sensitive=[]; still_sensitive=[]
for r in priced:
 event=parse(r['start']); scheduled=datetime.datetime.fromisoformat(r['ledger_kickoff']).replace(tzinfo=ks).astimezone(datetime.timezone.utc);gaps.append((event-scheduled).total_seconds()/60)
 for team in (r['home'],r['away']):
  cand=[x for x in g if team in (x['home'],x['away']) and parse(x['end'])<=event-datetime.timedelta(days=4)]
  if any(parse(x['end'])>scheduled-datetime.timedelta(days=4) for x in cand[-5:]):sensitive.append({'id':r['id'],'team':team})
  safe=[x for x in g if team in (x['home'],x['away']) and parse(x['end'])<=event-datetime.timedelta(days=4,minutes=30)]
  if any(parse(x['end'])>scheduled-datetime.timedelta(days=4) for x in safe[-5:]):still_sensitive.append({'id':r['id'],'team':team})
a={'official_schedule_sample_differences':differences,'price_ledger_schedule_assumed_kst':{'n':len(gaps),'min_first_event_delay_minutes':min(gaps),'max_first_event_delay_minutes':max(gaps),'original_profile_may_cross_four_day_cutoff':sensitive,'buffered_profile_crossings':still_sensitive},'note':'Five fixture times from official NBA sample; 411 archive kickoff timezone remains uncertified, so assumed KST is only a sensitivity check.'}
(P/'clock-audit.json').write_text(json.dumps(a,indent=2),encoding='utf8'); print(json.dumps(a,indent=2))
