"""Export a predetermined season from a frozen collector checkpoint without rewriting it."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(data): return hashlib.sha256(data).hexdigest()


def export(rows_path, lineups_path, output_path, year):
    rows_raw=Path(rows_path).read_bytes(); lineup_raw=Path(lineups_path).read_bytes()
    rows=[json.loads(x) for x in rows_raw.splitlines() if x.strip()]
    ids=[r['fixture_id'] for r in rows if r['season']==year]
    if len(ids)!=len(set(ids)): raise ValueError('duplicate_input_fixture')
    captured={}; game_ids=[]
    for line in lineup_raw.splitlines():
        if not line.strip(): continue
        fid=json.loads(line)['fixture_id']
        if fid in captured: raise ValueError('duplicate_collected_fixture')
        captured[fid]=line
    for fid in ids:
        if fid in captured:
            gid=json.loads(captured[fid]).get('official_game_id')
            if gid: game_ids.append(gid)
    duplicates=sum(n-1 for n in Counter(game_ids).values() if n>1)
    data=b''.join(captured[fid]+b'\n' for fid in ids if fid in captured)
    path=Path(output_path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f: f.write(data)
    return {'season':year,'season_denominator':len(ids),'exported':sum(fid in captured for fid in ids),
            'unprocessed':sum(fid not in captured for fid in ids), 'selection':'all input fixtures in specified season',
            'duplicate_official_game_ids':duplicates,
            'rows_sha256':digest(rows_raw),'checkpoint_sha256':digest(lineup_raw),'export_sha256':digest(data)}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rows',required=True);p.add_argument('--lineups',required=True)
    p.add_argument('--output',required=True);p.add_argument('--season',type=int,required=True);a=p.parse_args()
    print(json.dumps(export(a.rows,a.lineups,a.output,a.season),ensure_ascii=False,indent=2))
