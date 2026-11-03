"""Recheck immutable official evidence bundle hashes without network access."""
import json
from pathlib import Path
import source_adapter
P=Path(__file__).resolve().parent;REPOS={'2023':'C:/Users/user/Documents/Codex/kbtdata','2024':'C:/Users/user/Documents/Codex/kbt24','2025':'C:/Users/user/Documents/Codex/kbt25','2026':'C:/Users/user/Documents/Codex/kbtdata'}
def main():
 audit=json.loads((P/'inputs/source-raw-evidence-audit.json').read_bytes())
 for year,x in audit.items():
  raw=source_adapter.committed_blob(Path(REPOS[year]),x['commit'],x['path'],x['sha256'])
  if len(raw)!=x['bytes']:raise ValueError('source bundle size mismatch')
 print('Verified official raw evidence Git blobs:', ', '.join(sorted(audit)))
if __name__=='__main__':main()
