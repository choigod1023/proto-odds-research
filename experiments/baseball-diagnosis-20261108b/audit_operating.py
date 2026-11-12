import collections,gzip,hashlib,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
T=Path(r'C:\Users\user\AppData\Local\Temp\non-soccer-diagnosis-20261108'); p=T/'operating-ledger.jsonl.gz'; m=json.loads((T/'ledger-source-manifest.json').read_text(encoding='utf8')); assert hashlib.sha256(p.read_bytes()).hexdigest()==m['sha256']; data=[json.loads(x) for x in gzip.open(p,'rt',encoding='utf8')]; assert len(data)==m['rows']; settlements=collections.defaultdict(list)
for r in data:
 if r['record_type']=='settlement':settlements[r['snapshot_id']].append(r)
def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00'))
rows=[]
for r in data:
 if r['record_type']!='prediction' or r['features']['fixture']['sport']!='bs':continue
 f=r['features']['fixture']; pred=r['predictions']; h=r['features'].get('tipster_hybrid') or {}; ss=sorted(settlements[r['snapshot_id']],key=lambda x:x['ledger_sequence']); s=ss[-1] if ss else {}; o=s.get('outcome',{}); items=(pred.get('market_recommendations') or {}).get('items',[]); result=o.get('market_recommendation_results') or {}; featured=pred.get('featured_pick') or {}; fres=o.get('featured_pick_result') or {}; start=dt(r['kickoff']); evidence=h.get('evidence') or []
 for e in evidence:
  assert dt(e['observed_at'])<=start-timedelta(minutes=30)
 if o.get('result') in ('hit','miss'):assert o['selection_id']==pred['selection_id']
 mr=[]
 for i in items:
  v=result.get(i['offer_id']) or {}
  if v:assert all(v[k]==i[k] for k in ('offer_id','selection_id','market','line','settlement_rule'))
  mr.append({k:i.get(k) for k in ('market','line','odds','selection','selection_source','model','settlement_rule')}|{'result':v.get('result'),'net_units':v.get('net_units')})
 if fres: assert all(fres[k]==featured[k] for k in ('offer_id','selection_id','market','line','settlement_rule'))
 rows.append({'snapshot_id':r['snapshot_id'],'league':f['league'],'date_kst':(start+timedelta(hours=9)).date().isoformat(),'kickoff_utc':r['kickoff'],'captured_at':r['captured_at'],'minutes_before_kickoff':round((start-dt(r['captured_at'])).total_seconds()/60,2),'market_observed_at':r['market_observed_at'],'policy':r['model'].get('selection_policy'),'tipster_status':h.get('input_status',h.get('status')),'tipster_source':h.get('selection_source'),'authors':h.get('authors'),'evidence_count':len(evidence),'prediction':{k:pred.get(k) for k in ('market','selection','odds','probability','action')},'settlement':o.get('result'),'official_result':o.get('official_result'),'settlement_revisions':len(ss),'market_recommendations':mr,'featured':{k:featured.get(k) for k in ('market','selection','line','odds','settlement_rule')}|{'result':fres.get('result')} if featured else None,'player_status':{k:(r['features'].get('research_player_inputs') or {}).get(k) for k in ('status','observed_at','training_eligible','reason')}})
assert len({r['snapshot_id'] for r in rows})==len(rows)
out={'source_sha256':m['sha256'],'source_captured_at':m['captured_at'],'scope':m['selection'],'rows':rows,'limits':['API-selected snapshots only, not full ledger or full hash chain','kickoffs before 2026-10-28 absent; 14/30-day windows incomplete','captured_at is revision recording, not proof it is the final freeze','official settlement labels, not independent score reconfirmation','league/market opportunities beyond captured snapshot IDs omitted']}
Path('experiments/baseball-diagnosis-20261108b/operating-audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
for r in rows: print(r['date_kst'],r['league'],r['policy'],r['tipster_status'],r['tipster_source'],'authors',r['authors'],'odds',r['prediction']['odds'],'result',r['settlement'],'featured',r['featured'],'markets',r['market_recommendations'])
