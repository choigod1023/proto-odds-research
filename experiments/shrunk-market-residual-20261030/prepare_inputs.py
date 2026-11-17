"""One-time read-only extraction from exact upstream Git blobs; replay needs no sources."""
import gzip, hashlib, json, subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
BASE=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')
SOURCES=[
 ('cases','link-new-source-evidence-20261030','experiments/source-link-step2-20261030/inputs/cases.jsonl.gz'),
 ('history','baseball-league-fit-20261029','experiments/baseball-league-fit-20261029/history.jsonl'),
 ('canonical_targets','baseball-league-fit-20261029','experiments/baseball-league-fit-20261029/targets.jsonl'),
 ('official_phase2','link-new-source-evidence-20261030','experiments/source-link-baseball-20261030/phase2/features.jsonl.gz'),
 ('official_phase3','link-new-source-evidence-20261030','experiments/source-link-baseball-20261030/phase3/features.jsonl.gz'),
 ('mlb_calendar','pregame-feature-rd-20261030','experiments/pregame-rd-baseball-20261030/calendar-features.jsonl.gz'),
 ('mlb_exact','pregame-feature-rd-20261030','experiments/pregame-rd-baseball-20261030/features.jsonl.gz'),
]
def writegz(path,rows):
 data=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in rows).encode()
 path.write_bytes(gzip.compress(data,mtime=0))
def main():
 dest=HERE/'inputs';dest.mkdir(exist_ok=True);manifest=[]
 for name,tree,path in SOURCES:
  cwd=BASE/tree
  commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=cwd,text=True).strip()
  blob=subprocess.check_output(['git','show',f'{commit}:{path}'],cwd=cwd)
  assert (cwd/path).read_bytes()==blob,(name,'source worktree differs from committed blob')
  raw=gzip.decompress(blob) if path.endswith('.gz') else blob
  rows=[json.loads(s) for s in raw.decode('utf-8').splitlines()]
  if name=='history':
   rows=[{k:r[k]for k in ['league','date','home','away','hg','ag','source'] if k in r}|{k:r[k] for k in ['official_id','url','sources','game_type'] if k in r} for r in rows]
  elif name=='canonical_targets':
   rows=[{k:r[k]for k in ['league','fixture_id','home','away','date']} for r in rows]
  out=dest/(name+'.jsonl.gz');writegz(out,rows)
  manifest.append(dict(name=name,upstream_worktree=tree,upstream_commit=commit,path=path,source_sha256=hashlib.sha256(blob).hexdigest(),git_blob=subprocess.check_output(['git','rev-parse',f'{commit}:{path}'],cwd=cwd,text=True).strip(),projection=name in ('history','canonical_targets'),rows=len(rows),input_path='inputs/'+out.name,sha256=hashlib.sha256(out.read_bytes()).hexdigest()))
 # Preserve complete audit PR bodies/reports used; do not claim PR420 exists.
 audit=BASE/'accuracy-synthesis-20261030/.local-test/accuracy-pr-audit'
 history=[]
 for file in sorted(audit.glob('*-body.md')):
  history.append(dict(file=file.name,sha256=hashlib.sha256(file.read_bytes()).hexdigest(),text=file.read_text(encoding='utf-8')))
 for name in ['PR402-2026-10-29-year-model-hit-comparison.md','PR424-2026-10-30-pregame-feature-rd.md','PR425-2026-10-30-source-link-step2.md','PR425-2026-10-30-source-link-baseball.md']:
  file=audit/name;history.append(dict(file=name,sha256=hashlib.sha256(file.read_bytes()).hexdigest(),text=file.read_text(encoding='utf-8')))
 (dest/'prior-audit.json').write_text(json.dumps(dict(pr420='404 unavailable in parent audit; no inference',documents=history),ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 (HERE/'provenance.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
 print([(m['name'],m['rows'],m['upstream_commit'][:8])for m in manifest])
if __name__=='__main__':main()
