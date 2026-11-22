"""Audit packaged raw official responses and postgame statistics offline."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path


def sha(b):return hashlib.sha256(b).hexdigest()


def audit(normalized_path,bundle_path):
    gz=Path(normalized_path).read_bytes();bundle=Path(bundle_path).read_bytes()
    records=[json.loads(x) for x in gzip.decompress(gz).splitlines() if x.strip()]
    entries=[json.loads(x) for x in gzip.decompress(bundle).splitlines() if x.strip()]
    by_sha={}
    for item in entries:
        meta=item['metadata'];raw=base64.b64decode(item['raw_base64'],validate=True)
        if sha(raw)!=meta['sha256'] or len(raw)!=meta['bytes']:raise ValueError('raw_mismatch')
        by_sha[meta['sha256']]=raw
    ids=set(); counts={}
    for r in records:
        gid=r['official_game_id']
        if gid in ids:raise ValueError('duplicate_game')
        ids.add(gid);counts[r['status']]=counts.get(r['status'],0)+1
        if not '20260727'<=r['game_date']<='20261027':raise ValueError('out_of_window')
        if r['status']!='ok':continue
        ev=r['evidence']
        for label in ('box','scroll','schedule'):
            if ev[label]['sha256'] not in by_sha:raise ValueError('evidence_missing')
        if any(ev[name]['params']['gameId']!=gid for name in ('box','scroll')):raise ValueError('request_identity')
        sched=json.loads(by_sha[ev['schedule']['sha256']]);game=[x for x in sched['game'] if x['G_ID']==gid]
        if len(game)!=1 or game[0]['G_DT']!=r['game_date'] or game[0]['HOME_ID']!=r['team_ids']['home'] or game[0]['AWAY_ID']!=r['team_ids']['away']:
            raise ValueError('game_identity')
        if r['historical_receipt_proven'] is not False or r['target_game_outcome_not_feature'] is not True:raise ValueError('unsafe_time_claim')
        for side in ('home','away'):
            hitters=r['statistics']['batters'][side];pitchers=r['statistics']['pitchers'][side]
            if sum(x['announced_lineup_match'] for x in hitters)<6 or sum(x['is_starter'] for x in pitchers)!=1:raise ValueError('invalid_team_lineup')
            for p in hitters:
                if any(p[k] is not None for k in ('BB','HR','K','2B','3B')):raise ValueError('fabricated_batter_field')
            for p in pitchers:
                if p['BB'] is not None:raise ValueError('combined_walks_promoted')
                for key in ('outs','batters_faced','pitch_count','H','HR','BB_plus_HBP','K','R','ER'):
                    if p[key] is not None and (type(p[key]) is not int or p[key]<0):raise ValueError('invalid_pitcher_value')
    return {'records':len(records),'statuses':counts,'raw_entries':len(entries),
            'normalized_gzip_sha256':sha(gz),'raw_bundle_gzip_sha256':sha(bundle),
            'audit':'local_only_no_network','historical_receipt_proven':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('records');p.add_argument('bundle');a=p.parse_args()
    print(json.dumps(audit(a.records,a.bundle),ensure_ascii=False,indent=2))
