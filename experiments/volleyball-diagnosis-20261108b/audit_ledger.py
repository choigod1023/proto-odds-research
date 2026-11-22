"""Audit supplied selected ledger only. Never connects to production."""
import collections, gzip, hashlib, json, sys
from datetime import datetime, timedelta
from pathlib import Path

def stamp(s): return datetime.fromisoformat(s.replace('Z','+00:00'))
def audit(path):
 raw=Path(path).read_bytes(); rows=[json.loads(x) for x in gzip.decompress(raw).splitlines()]; settlements=collections.defaultdict(list)
 for row in rows:
  if row['record_type']=='settlement': settlements[row['snapshot_id']].append(row)
 out=[]
 for r in rows:
  if r['record_type']!='prediction' or r['features'].get('fixture',{}).get('sport')!='vl': continue
  f=r['features']['fixture']; p=r['predictions']; t=r['features'].get('tipster_hybrid',{}); s=sorted(settlements[r['snapshot_id']],key=lambda a:a['ledger_sequence']); latest=s[-1] if s else None
  out.append({'snapshot_id':r['snapshot_id'],'event_id':r['event_id'],'league':f['league'],'date':f['date'],'kickoff':r['kickoff'],'captured_at':r['captured_at'],'as_of':r['as_of'],'before_t30_captured':stamp(r['captured_at'])<=stamp(r['kickoff'])-timedelta(minutes=30),'before_t30_asof':stamp(r['as_of'])<=stamp(r['kickoff'])-timedelta(minutes=30),'market_observed_at':r['market_observed_at'],'policy':r['model'].get('selection_policy'),'selection':p.get('selection'),'market':p.get('market'),'odds':p.get('odds'),'authors':t.get('authors'),'selection_source':t.get('selection_source'),'reason':t.get('reason'),'settlement_revisions':len(s),'result':latest['outcome'].get('result') if latest else None,'official_result':latest['outcome'].get('official_result') if latest else None,'selection_id_matches':latest['outcome'].get('selection_id')==p.get('selection_id') if latest else None,'featured':bool(p.get('featured_pick'))})
 return {'input_sha256':hashlib.sha256(raw).hexdigest(),'rows':out}
if __name__=='__main__':
 result=audit(sys.argv[1]); Path(__file__).with_name('ledger-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
