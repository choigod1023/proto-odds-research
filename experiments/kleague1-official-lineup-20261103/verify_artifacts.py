"""Verify artifact and original HTTP response bytes without network."""
import gzip,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parent

def digest(b):return hashlib.sha256(b).hexdigest()
def main():
    handoff=json.loads((B/'handoff.json').read_text(encoding='utf8'))
    for path,expected in handoff['artifact_sha256s'].items():
        if digest((B/path).read_bytes())!=expected:raise ValueError('artifact hash mismatch: '+path)
    checked=0
    for file in ('requests.json','schedule-requests.json'):
        for e in json.loads((B/file).read_text(encoding='utf8')):
            if not e.get('raw_file'):continue
            raw=(B/e['raw_file']).read_bytes()
            if e['raw_file'].endswith('.gz'):raw=gzip.decompress(raw)
            if digest(raw)!=e['sha256'] or len(raw)!=e['bytes']:raise ValueError('response hash mismatch: '+e['raw_file'])
            checked+=1
    cov=json.loads((B/'coverage-2026.json').read_text(encoding='utf8'))
    if cov['fixtures_sha256']!=digest((B/'fixtures.jsonl').read_bytes()) or cov['plan_sha256']!=digest((B/'cohort-plan.json').read_bytes()):raise ValueError('coverage input hash mismatch')
    pilot=json.loads((B/'crosswalk.json').read_text(encoding='utf8'))
    if pilot['fixtures_sha256']!=digest((B/pilot['fixture_file']).read_bytes()):raise ValueError('pilot input hash mismatch')
    print('artifact files',len(handoff['artifact_sha256s']),'HTTP response bytes',checked,'verified')
if __name__=='__main__':main()
