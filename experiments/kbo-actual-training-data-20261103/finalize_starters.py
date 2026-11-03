"""Freeze a complete actual-starter checkpoint and explicit identity-only retries."""
import argparse,collections,hashlib,json,os
from pathlib import Path
C=Path('.research-state/kbo-actual-2023')
a=argparse.ArgumentParser();a.add_argument('--season',type=int,choices=(2023,2026),required=True);args=a.parse_args();season=args.season;expected=723 if season==2023 else 709
progress=json.loads((C/f'actual-starters-{season}-progress.json').read_text(encoding='utf8'))
if progress['population']!=expected or progress['processed']!=expected or progress['remaining']!=0 or progress['stop_reason']!='complete':raise ValueError('starter_capture_not_finished')
source=[json.loads(x) for x in (C/f'actual-starters-{season}.jsonl').read_text(encoding='utf8').splitlines()];allrows={x['fixture_id']:x for x in source}
if len(source)!=expected or len(allrows)!=expected:raise ValueError('starter_capture_duplicates_or_missing')
retry=C/f'actual-starters-{season}-retry.jsonl'
if retry.exists():
 attempted=set()
 for x in map(json.loads,retry.read_text(encoding='utf8').splitlines()):
  fid=x['fixture_id'];old=allrows[fid]
  if fid in attempted or old['status']!='excluded':raise ValueError('invalid_starter_retry')
  attempted.add(fid)
  if x['status']=='ok':x['previous_exclusion_reason']=old.get('reason');allrows[fid]=x
  else:old['retry_reason']=x.get('reason')
out=C/f'actual-starters-{season}-final.jsonl';tmp=out.with_suffix('.jsonl.tmp');tmp.write_text(''.join(json.dumps(allrows[f],ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for f in sorted(allrows)),encoding='utf8',newline='\n');os.replace(tmp,out)
summary={'season':season,'population':expected,'statuses':dict(collections.Counter(x['status'] for x in allrows.values())),'excluded_reasons':dict(collections.Counter(x.get('reason') for x in allrows.values() if x['status']=='excluded')),'recovered':sum(bool(x.get('previous_exclusion_reason')) for x in allrows.values()),'sha256':hashlib.sha256(out.read_bytes()).hexdigest()};(C/f'actual-starters-{season}-final-manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(summary,ensure_ascii=False))
