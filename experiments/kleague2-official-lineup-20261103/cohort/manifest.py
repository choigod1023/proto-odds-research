"""Create/verify exact bytes for all raw and derived research data."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'cohort'/'manifest.json'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def entries():return {p.relative_to(ROOT).as_posix():digest(p) for p in sorted(ROOT.rglob('*')) if p.is_file() and p.suffix in ('.gz','.json','.jsonl','.bin') and p!=OUT}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--write',action='store_true');args=a.parse_args()
 actual=entries()
 if args.write:OUT.write_bytes((json.dumps({'sha256_exact_git_bytes':actual},sort_keys=True,indent=2)+'\n').encode('utf8'))
 else:
  expected=json.loads(OUT.read_bytes())['sha256_exact_git_bytes']
  mismatch=sorted(set(k for k in expected.keys()|actual.keys() if expected.get(k)!=actual.get(k)))
  print(f'files={len(actual)} mismatches={len(mismatch)}')
  if mismatch:raise SystemExit('\n'.join(mismatch))
