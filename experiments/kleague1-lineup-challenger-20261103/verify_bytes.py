"""Verify registered input, frozen, and evaluation bytes without external access."""
import hashlib,json
from pathlib import Path
b=Path(__file__).resolve().parent
h=json.loads((b/'handoff.json').read_bytes())
for path,expected in h['artifact_sha256s'].items():
 if hashlib.sha256((b/path).read_bytes()).hexdigest()!=expected:raise ValueError('artifact SHA mismatch: '+path)
print('artifact files verified:',len(h['artifact_sha256s']))
