"""Inspect input identity and clocks without fitting or reading model output."""
import argparse
from collections import defaultdict
from datetime import datetime,timedelta,timezone
import gzip,hashlib,json
from pathlib import Path

def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(x) for x in f if x.strip()]
def instant(s):
    value=datetime.fromisoformat(s.replace('Z','+00:00'))
    if value.utcoffset() is None:raise ValueError('naive timestamp')
    return value.astimezone(timezone.utc)
def main():
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    paths={name:a.inputs/name for name in ('targets.jsonl.gz','votes.jsonl.gz','cases.jsonl.gz')}
    targets,frames,cases=(read(paths[x]) for x in paths)
    fmap=defaultdict(list);cmap=defaultdict(list)
    for f in frames:fmap[f['event_id']].append(f)
    for c in cases:cmap[(c['cohort'],c['league'],c['event_id'])].append(c)
    counts=defaultdict(int); errors=[]; seen=set()
    for t in targets:
        if t.get('actual_votes') is not True:continue
        eid=t['event_id'];fs=fmap[eid];cs=cmap[(t['cohort'],t['league'],eid)]
        if eid in seen or len(fs)!=1 or len(cs)!=1:errors.append((eid,'duplicate/missing identity'));continue
        seen.add(eid);f=fs[0];c=cs[0]
        try:
            if (f['league']!=t['league'] or f['event_id']!=eid or [f['home'],f['away']]!=[c['home'],c['away']]
                or t['identity_bridge']['official_fixture_id']!=eid or t['identity_bridge']['source_names']!=[c['home'],c['away']]
                or t['identity_bridge']['canonical_names']!=[t['home'],t['away']] or instant(f['kickoff'])!=instant(c['kickoff'])
                or instant(f['kickoff'])!=instant(t['kickoff']) or any(t[k]!=c[k] for k in ('sides','odds','market_p','outcome'))):
                raise ValueError('identity/price/outcome mismatch')
            decision=instant(t['decision_at']);kickoff=instant(t['kickoff'])
            if decision!=instant(c['decision_at']) or decision>kickoff-timedelta(minutes=30):raise ValueError('decision mismatch or late')
            late=0;unknown=0
            for vote in f['raw_forecasts']:
                try:
                    if instant(vote['source_reported_published_at'])>decision:late+=1
                except (KeyError,ValueError,TypeError,AttributeError):unknown+=1
            counts['events']+=1;counts['vote_claims_late']+=late;counts['vote_claims_unknown']+=unknown
            if t.get('source_receipt_certified') is True:counts['claimed_certified_target_rows']+=1
        except (KeyError,ValueError,TypeError,AttributeError) as ex:errors.append((eid,str(ex)))
    r={'sha256':{k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in paths.items()},'counts':dict(counts),'errors':errors,'caution':'claim timestamps and stored fields only; no independent receipt, price, result or original raw byte authentication'}
    a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'counts':r['counts'],'errors':len(errors)}))
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
