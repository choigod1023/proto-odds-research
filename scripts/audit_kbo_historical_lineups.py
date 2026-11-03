"""Re-check saved KBO response hashes, event identity and lineup/profile evidence."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
from research_kbo_historical_lineups import parse_lineup, profile_name


def audit(directory):
    directory=Path(directory)
    def raw(meta):
        data=(directory/'raw'/meta['sha256']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=meta['sha256']: raise ValueError('raw_hash_mismatch')
        if meta['http_status']!=200: raise ValueError('http_status_not_success')
        return data
    output=directory/'official-lineups.jsonl'
    rows=[json.loads(line) for line in output.read_text(encoding='utf8').splitlines() if line.strip()]
    if len({r['fixture_id'] for r in rows})!=len(rows): raise ValueError('duplicate_fixture')
    counts=collections.Counter()
    for row in rows:
        counts[row['lineup_status']]+=1
        if row.get('eligible_as_pregame_feature') is not False: raise ValueError('unsafe_historical_eligibility')
        if row['lineup_status']=='unresolved': continue
        schedule=json.loads(raw(row['schedule_evidence']))
        games=[g for g in schedule['game'] if g['G_ID']==row['official_game_id']]
        if len(games)!=1: raise ValueError('schedule_not_unique')
        game=games[0]
        if (game['HOME_ID'],game['AWAY_ID'],game['G_DT']) != (row['home_team_id'],row['away_team_id'],row['game_date']):
            raise ValueError('schedule_identity_mismatch')
        for side,prefix in [('home','B'),('away','T')]:
            pitcher=row['starting_pitchers'][side]
            if pitcher['player_id'] != str(game.get(prefix+'_PIT_P_ID') or '') or pitcher['name'] != game.get(prefix+'_PIT_P_NM'):
                raise ValueError('starting_pitcher_field_mismatch')
        parsed=parse_lineup(raw(row['lineup_evidence']),game)
        if parsed is None:
            if row['lineup_flag'] is not False or row['lineups'] is not None: raise ValueError('false_flag_promoted')
            continue
        if row['lineup_flag'] is not True: raise ValueError('flag_mismatch')
        used=set()
        for side in ('home','away'):
            actual=row['lineups'][side]
            if [{k:p[k] for k in ('order','position','name')} for p in actual] != parsed[side]:
                raise ValueError('lineup_content_mismatch')
            if row['lineup_status']=='official_flag_true_profile_name_crossmatched':
                for p in actual:
                    if p['player_id'] in used: raise ValueError('duplicate_player_id')
                    used.add(p['player_id'])
                    evidence=p['official_profile_evidence']
                    if str(evidence['params']['playerId'])!=p['player_id'] or profile_name(raw(evidence))!=p['name']:
                        raise ValueError('profile_mismatch')
    return {'lineups_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'rows':len(rows),'counts':dict(counts),'checked_at':'local_audit_no_new_network'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args();print(json.dumps(audit(a.directory),ensure_ascii=False,indent=2))
