"""Verify DC artifacts and preserve prior Poisson evidence hashes."""
import argparse,hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent;OUT=R/'manifest.json';PRIOR=R.parent/'kleague2-lineup-challenger-20261103'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def entries():return {p.relative_to(R).as_posix():sha(p) for p in sorted(R.rglob('*')) if p.is_file() and p.suffix in ('.py','.json','.jsonl') and p!=OUT}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--write',action='store_true');x=a.parse_args();actual=entries()
 if x.write:OUT.write_bytes((json.dumps({'sha256_exact_bytes':actual},sort_keys=True,indent=2)+'\n').encode())
 else:
  expected=json.loads(OUT.read_bytes())['sha256_exact_bytes'];bad=sorted(k for k in actual.keys()|expected.keys() if actual.get(k)!=expected.get(k));print('DC files',len(actual),'mismatches',len(bad));
  if bad:raise SystemExit('\n'.join(bad))
