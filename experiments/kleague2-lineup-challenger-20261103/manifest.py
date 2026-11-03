"""Verify canonical bytes for model, evidence, freeze and results."""
import argparse,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent; OUT=R/'manifest.json'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def entries():return {p.relative_to(R).as_posix():digest(p) for p in sorted(R.rglob('*')) if p.is_file() and p.suffix in ('.py','.json','.jsonl','.gz') and p!=OUT}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--write',action='store_true');arg=a.parse_args();actual=entries()
 if arg.write:OUT.write_bytes((json.dumps({'sha256_exact_bytes':actual},indent=2,sort_keys=True)+'\n').encode())
 else:
  expected=json.loads(OUT.read_bytes())['sha256_exact_bytes'];bad=sorted(k for k in actual.keys()|expected.keys() if actual.get(k)!=expected.get(k));print('files',len(actual),'mismatches',len(bad))
  if bad:raise SystemExit('\n'.join(bad))
