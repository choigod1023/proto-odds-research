import csv, gzip, hashlib, io, json, tarfile, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
EXPECTED = 'a5d1bed30778430816b755676649cb2e3690ea62df5d96f4d9d6e9a8b8839339'

def extract(archive, sample, output):
    if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('archive hash mismatch')
    source = [json.loads(line) for line in Path(sample).read_text(encoding='utf8').splitlines()]
    fixtures = {x['payload']['source_fixture_id']: x for x in source if x['kind'] == 'fixture'}
    results = {x['payload']['source_fixture_id']: x for x in source if x['kind'] == 'result'}
    selected = {str(int(x)): x for x in fixtures}
    rows = {x: [] for x in fixtures}
    with tarfile.open(archive, 'r:xz') as tar:
        members = tar.getmembers()
        if len(members) != 1 or members[0].name != 'cdnnba_2025.csv':
            raise ValueError('unexpected archive layout')
        with io.TextIOWrapper(tar.extractfile(members[0]), encoding='utf8', newline='') as f:
            for row in csv.DictReader(f):
                if row['gameId'] in selected:
                    rows[selected[row['gameId']]].append(row)
    with open(output, 'wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as z:
            for gid in sorted(rows):
                for row in rows[gid]:
                    z.write((json.dumps({'official_game_id':gid, 'event':row}, ensure_ascii=False, sort_keys=True)+'\n').encode())
    audit = {}
    for gid, events in rows.items():
        scores = [(int(e['scoreHome']), int(e['scoreAway'])) for e in events if e['scoreHome'].isdigit() and e['scoreAway'].isdigit()]
        audit[gid] = {'events': len(events), 'substitutions': sum(e['actionType']=='substitution' for e in events), 'last_score': scores[-1] if scores else None, 'official_result_payload': results.get(gid, {}).get('payload'), 'fixture': fixtures[gid]['payload'], 'start5_available': False}
    return audit

if __name__ == '__main__':
    result = extract(sys.argv[1], ROOT/'experiments/pregame-basketball-20261104/nba-2026-sample.jsonl', OUT/'events.jsonl.gz')
    (OUT/'event-audit.json').write_text(json.dumps(result, indent=2), encoding='utf8')
