"""Copy a stopped KBO checkpoint; reject conflicting duplicate results."""
import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def sha(raw): return hashlib.sha256(raw).hexdigest()


def semantic(row):
    sides = row.get('lineups')
    if sides is not None:
        sides = {side:[{k:p.get(k) for k in ('order','position','name','player_id')}
                       for p in sides[side]] for side in ('home','away')}
    return {'official_game_id':row.get('official_game_id'),'home_team_id':row.get('home_team_id'),
            'away_team_id':row.get('away_team_id'),'lineup_flag':row.get('lineup_flag'),
            'lineup_status':row.get('lineup_status'),'lineups':sides,'reason':row.get('reason')}


def recover(source, destination):
    source=Path(source); destination=Path(destination)
    if destination.exists(): raise ValueError('destination_exists')
    raw=(source/'official-lineups.jsonl').read_bytes()
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    latest={}; counts=Counter()
    for row in rows:
        fid=row['fixture_id']; counts[fid]+=1
        if fid in latest and semantic(latest[fid]) != semantic(row):
            raise ValueError('conflicting_duplicate:'+fid)
        for entry in [row.get('schedule_evidence'),row.get('lineup_evidence')]+[
                player.get('official_profile_evidence') for side in (row.get('lineups') or {}).values() for player in side]:
            if entry:
                body=(source/'raw'/entry['sha256']).read_bytes()
                if sha(body)!=entry['sha256']: raise ValueError('raw_hash_mismatch:'+fid)
        latest[fid]=row
    destination.mkdir(parents=True)
    shutil.copytree(source/'raw',destination/'raw')
    shutil.copytree(source/'requests',destination/'requests')
    data=''.join(json.dumps(v,ensure_ascii=False,sort_keys=True)+'\n' for v in latest.values()).encode('utf8')
    (destination/'official-lineups.jsonl').write_bytes(data)
    info={'source':str(source),'source_sha256':sha(raw),'source_lines':len(rows),
          'unique_fixtures':len(latest),'duplicate_rows':len(rows)-len(latest),
          'duplicate_fixture_count':sum(v>1 for v in counts.values()),'semantic_conflicts':0,
          'recovered_sha256':sha(data),'policy':'latest identical batting semantics; preserve source checkpoint and raw cache'}
    (destination/'recovery.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(info,ensure_ascii=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination');a=p.parse_args();recover(a.source,a.destination)
