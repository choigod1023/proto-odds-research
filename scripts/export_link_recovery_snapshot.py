"""Bounded read-only research export; never initializes or writes the live DB."""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import subprocess


def collect(connection, exported_at):
    """Only public sports inputs, not wallets, users, secrets or environment."""
    def rows(sql, args=()):
        cursor = connection.execute(sql, args)
        keys = [x[0] for x in cursor.description]
        return [dict(zip(keys, row)) for row in cursor]
    documents = rows("""SELECT name,revision,generated_at,stored_at,content_hash,payload_json
        FROM documents WHERE name IN ('kbo_pregame','kbo_model_candidate_state',
        'kbo_candidate_calendar_cache','public_tipster_intake') ORDER BY name""")
    records = rows("""SELECT ledger_sequence,record_type,event_id,snapshot_id,
        captured_at,record_hash,record_json FROM prediction_records
        ORDER BY ledger_sequence DESC LIMIT 256""")
    # Narrow an indexed time range before resolving the physical KBO fixture.
    prices = rows("""SELECT observed_at,season,round,game_no,sport,league,
        market_family,n_way,market_label,home,away,date_text,odds_json,result
        FROM odds_snapshots WHERE observed_at>=? AND observed_at<=?
        AND sport='bs' AND date_text LIKE '11.01%'
        AND home IN ('삼성','삼성라이온즈') AND away IN ('KIA','KIA타이거즈')
        ORDER BY observed_at,round,game_no LIMIT 2000""",
        ('2026-10-30T00:00:00+00:00','2026-11-01T04:30:00+00:00'))
    streams = rows("""SELECT stream,count(*) AS rows,min(observed_at) AS first_at,
        max(observed_at) AS last_at FROM event_records
        WHERE stream IN ('pregame_player_capture','public_tipster_votes',
        'tailslips_first_observed','public_tipster_intake') GROUP BY stream""")
    for row in documents:
        row['payload'] = json.loads(row.pop('payload_json'))
    for row in records:
        row['record'] = json.loads(row.pop('record_json'))
    for row in prices:
        row['odds'] = json.loads(row.pop('odds_json'))
    return {'schema_version':'link-recovery-snapshot-v1', 'exported_at':exported_at,
        'mode':'read_only', 'scope':{'prediction_tail_limit':256,
            'price_row_limit':2000,'price_period':'2026-10-30..2026-11-01T04:30Z',
            'fixture':'2026-11-01 Samsung home vs KIA away',
            'complete_ledger':False,'independent_receipt_authentication':False},
        'documents':documents,'prediction_tail':records,'fixture_prices':prices,
        'event_stream_metadata':streams}


def remote_source():
    return ('import sqlite3,json,gzip,base64,hashlib\n'
        'from datetime import datetime,timezone\n' + inspect.getsource(collect) + '''
c=sqlite3.connect('file:/data/proodd.sqlite3?mode=ro',uri=True)
c.execute('BEGIN')
data=collect(c,datetime.now(timezone.utc).isoformat())
c.close()
raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf8')
wire=base64.b64encode(gzip.compress(raw,compresslevel=1)).decode('ascii')
print('LINK_SNAPSHOT_BEGIN')
for offset in range(0,len(wire),4096):print(wire[offset:offset+4096])
print('LINK_SNAPSHOT_SHA256 '+hashlib.sha256(raw).hexdigest())
print('LINK_SNAPSHOT_END')
''')


def decode_transport(stdout):
    lines = stdout.splitlines()
    begin, end = lines.index('LINK_SNAPSHOT_BEGIN'), lines.index('LINK_SNAPSHOT_END')
    digest_line = lines[end-1]
    if not digest_line.startswith('LINK_SNAPSHOT_SHA256 '):
        raise ValueError('missing snapshot digest')
    wire = base64.b64decode(''.join(lines[begin+1:end-1]), validate=True)
    raw = gzip.decompress(wire)
    if hashlib.sha256(raw).hexdigest() != digest_line.split()[1]:
        raise ValueError('snapshot transport hash mismatch')
    payload = json.loads(raw)
    if payload.get('schema_version') != 'link-recovery-snapshot-v1':
        raise ValueError('unexpected snapshot schema')
    return raw, payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing capture')
    result = subprocess.run(['flyctl','ssh','console','--app','proto-odds-collector',
        '-C','python3 -'], input=remote_source().encode(), capture_output=True, timeout=90)
    raw, payload = decode_transport(result.stdout.decode('utf8', errors='strict'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as f:
        f.write(raw)
    print(json.dumps({'output':str(args.output.resolve()),'sha256':hashlib.sha256(raw).hexdigest(),
        'documents':len(payload['documents']),'prediction_tail':len(payload['prediction_tail']),
        'fixture_prices':len(payload['fixture_prices']),'ssh_exit_code':result.returncode,
        'transport_verified':True,'scope':payload['scope']},ensure_ascii=False))


if __name__ == '__main__':
    main()
