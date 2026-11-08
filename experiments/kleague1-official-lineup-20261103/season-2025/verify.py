"""Verify immutable 2025 snapshot, original response bytes and prior 2026 cohort."""
import gzip,hashlib,json,subprocess,sys
from pathlib import Path
B=Path(__file__).resolve().parent
ROOT=B.parent

def sha(x):return hashlib.sha256(x).hexdigest()
def verify():
    prior=subprocess.run([sys.executable,str(ROOT/'verify_artifacts.py')],capture_output=True,text=True,check=True)
    h=json.loads((B/'handoff.json').read_bytes())
    if sha((ROOT/'handoff.json').read_bytes())!=h['prior_2026_handoff_sha256']:raise ValueError('prior 2026 handoff differs')
    for path,expected in h['artifact_sha256s'].items():
        if sha((B/path).read_bytes())!=expected:raise ValueError('2025 artifact hash mismatch: '+path)
    latest=json.loads((B/'latest.json').read_bytes()); snap=B/latest['snapshot']; manifest_bytes=(snap/'manifest.json').read_bytes()
    if sha(manifest_bytes)!=latest['manifest_sha256']:raise ValueError('snapshot manifest hash mismatch')
    m=json.loads(manifest_bytes)
    for name,key in (('fixtures.jsonl','fixtures_sha256'),('timeline.jsonl','timeline_sha256')):
        if sha((snap/name).read_bytes())!=m[key]:raise ValueError('snapshot derived file hash mismatch: '+name)
    for name,key in (('plan.json','plan_sha256'),('schedule-requests.json','schedule_requests_sha256')):
        if sha((B/name).read_bytes())!=m[key]:raise ValueError('frozen plan or schedule log mismatch: '+name)
    # Current log may be longer after a later resumed run. This snapshot stores its own
    # immutable log by hash, so snapshot verification never reinterprets later receipts.
    logbytes=(snap/'requests.json').read_bytes()
    if sha(logbytes)!=m['request_log_sha256']:raise ValueError('snapshot request log mismatch')
    entries=json.loads(logbytes);checked=0
    for e in entries:
        name=e.get('raw_file')
        if not name:continue
        archive=(B/name).read_bytes()
        if sha(archive)!=m['source_archives_sha256'][name]:raise ValueError('archive hash mismatch')
        body=gzip.decompress(archive) if name.endswith('.gz') else archive
        if sha(body)!=e['sha256'] or len(body)!=e['bytes']:raise ValueError('response bytes mismatch')
        checked+=1
    schedule_logs=json.loads((B/'schedule-requests.json').read_bytes())
    for e in schedule_logs:
        name='schedule-2025-'+e['payload']['month']+'.json';raw=(B/'raw'/name).read_bytes()
        if sha(raw)!=m['schedule_sha256s'][name] or sha(raw)!=e['sha256']:raise ValueError('schedule bytes mismatch')
    if m['lineup_and_result_verified']+m['pending_count']+m['failed_count']!=m['scheduled']:raise ValueError('coverage partition mismatch')
    print(prior.stdout.strip());print('2025 HTTP response bytes',checked,'schedule response bytes',len(m['schedule_sha256s']),'verified')
if __name__=='__main__':verify()
