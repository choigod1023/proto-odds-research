"""Freeze complete 2023 lineup checkpoints and explicit exact-game-ID retries."""
import json,os,hashlib,collections
from pathlib import Path
C=Path('.research-state/kbo-actual-2023');R=Path(r'C:\Users\user\Documents\Codex\kbodata\experiments\kbo-player-data-20261103\rows.jsonl')
manifest=json.loads((C/'manifest.json').read_text(encoding='utf8'))
if manifest['selection_rule']['only_season']!=2023 or manifest['selected_denominator']!=723 or manifest['selected_processed']!=723 or manifest['stop_reason']!='complete':raise ValueError('lineup_capture_not_finished')
ids={r['fixture_id'] for r in map(json.loads,R.read_text(encoding='utf8').splitlines()) if r['season']==2023};source=[x for x in map(json.loads,(C/'official-lineups.jsonl').read_text(encoding='utf8').splitlines()) if x['fixture_id'] in ids];allrows={x['fixture_id']:x for x in source}
if len(ids)!=723 or len(source)!=723 or set(allrows)!=ids:raise ValueError('lineup_capture_cohort_duplicate_or_missing')
retry=C/'retry-lineups.jsonl'
if retry.exists():
 for x in map(json.loads,retry.read_text(encoding='utf8').splitlines()):
  fid=x['fixture_id'];old=allrows[fid]
  if old['lineup_status']!='unresolved' or old.get('reason') not in ('kickoff_mismatch','game_not_uniquely_in_official_schedule'):raise ValueError('invalid_retry_override')
  if x.get('lineup_status')=='unresolved':
   old['retry_reason']=x.get('reason');continue
  if not (x.get('kickoff_conflict') or x.get('fixture_match_basis')=='encoded_year_alias_exact_official_schedule_match'):raise ValueError('retry_evidence_missing')
  x['previous_exclusion_reason']=old['reason'];allrows[fid]=x
official_ids=[x['official_game_id'] for x in allrows.values() if x.get('official_game_id')]
if len(official_ids)!=len(set(official_ids)):raise ValueError('duplicate_official_game_id_alias')
out=C/'official-lineups-2023-final.jsonl';tmp=out.with_suffix('.jsonl.tmp');tmp.write_text(''.join(json.dumps(allrows[f],ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for f in sorted(ids)),encoding='utf8',newline='\n');os.replace(tmp,out)
summary={'population':723,'statuses':dict(collections.Counter(x['lineup_status'] for x in allrows.values())),'official_crosswalks':dict(collections.Counter(x.get('fixture_match_basis') for x in allrows.values())),'recovered_kickoff_conflict':sum(bool(x.get('kickoff_conflict')) for x in allrows.values()),'sha256':hashlib.sha256(out.read_bytes()).hexdigest()};(C/'official-lineups-2023-final-manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(summary,ensure_ascii=False))
