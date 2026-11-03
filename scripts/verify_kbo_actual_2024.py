"""Independent artifact and evidence checks; performs no network requests."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

from research_kbo_actual_2024_capture import rows
from research_kbo_actual_2024_finalize import OUT, PITCHERS, profile_name


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check_identity_only(value):
    if isinstance(value, dict):
        forbidden = {'bf','hit','hr','rbi','era','ab','score','pitch_count','runs','outcome','odds'}
        assert not (set(value) & forbidden)
        for child in value.values():
            check_identity_only(child)
    elif isinstance(value, list):
        for child in value:
            check_identity_only(child)


def main():
    manifest = json.loads((OUT/'manifest.json').read_bytes())
    roster_bytes = (OUT/'rosters.jsonl').read_bytes()
    extract_bytes = (OUT/'batter-identity-only.json').read_bytes()
    bundle_bytes = (OUT/'official-evidence.tar.gz').read_bytes()
    assert digest(roster_bytes) == manifest['rosters_sha256']
    assert digest(extract_bytes) == manifest['batter_identity_extract_sha256']
    assert digest(bundle_bytes) == manifest['evidence_bundle_sha256']
    pitcher_source = PITCHERS.read_bytes()
    assert digest(pitcher_source) == manifest['pitcher_identity_source_sha256']
    pitcher_games = json.loads(pitcher_source)
    extract = json.loads(extract_bytes)
    roster = [json.loads(x) for x in roster_bytes.splitlines()]
    check_identity_only(roster)
    source = rows()
    assert len(roster) == len(source) == 728
    assert [x['fixture_id'] for x in roster] == [x['fixture_id'] for x in source]
    assert all(set(p) == {'code','name','order'} for game in extract.values() for side in ('home','away') for p in game[side])
    eligible = [x for x in roster if not x['exclusion_reasons']]
    assert len(eligible) == manifest['eligible']
    assert len({x['official_game_id'] for x in eligible}) == len(eligible)
    checked_raw = {}
    profile_names = {}
    with tarfile.open(OUT/'official-evidence.tar.gz', mode='r:gz') as tar:
        compressed_raw = {member.name:tar.extractfile(member).read() for member in tar if member.isfile() and member.name.startswith('raw/')}
        def evidence(meta):
            sha = meta['sha256']
            if sha not in checked_raw:
                data = gzip.decompress(compressed_raw['raw/' + sha + '.gz'])
                assert digest(data) == sha and len(data) == meta['bytes']
                checked_raw[sha] = data
            return checked_raw[sha]
        def official_profile_name(meta):
            sha = meta['sha256']
            if sha not in profile_names:
                profile_names[sha] = profile_name(evidence(meta))
            return profile_names[sha]
        for result in eligible:
            gid = result['official_game_id']
            ev = result['evidence']
            for name in ('lineup_evidence','boxscore_evidence'):
                assert ev[name]['params']['gameId'] == gid
            schedule = json.loads(evidence(ev['schedule_evidence']))
            official = [x for x in schedule['game'] if x['G_ID'] == gid]
            assert len(official) == 1
            game = official[0]
            assert game['HOME_NM'] == result['home_team'] and game['AWAY_NM'] == result['away_team']
            assert str(game['SEASON_ID']) == '2024' and str(game['LE_ID']) == '1'
            lineup = json.loads(evidence(ev['lineup_evidence']))
            box = json.loads(evidence(ev['boxscore_evidence']))
            assert lineup[0][0]['LINEUP_CK'] is True and str(box['code']) == '100'
            seen = set()
            for side, mi, li, bi, prefix in [('away',2,4,0,'T'),('home',1,3,1,'B')]:
                meta = lineup[mi][0]
                for field, expected in [('G_ID',gid),('T_ID',game[side.upper()+'_ID']),('LE_ID',game['LE_ID']),('SR_ID',game['SR_ID']),('SEASON_ID',game['SEASON_ID'])]:
                    assert str(meta[field]) == str(expected)
                official_lineup = json.loads(lineup[li][0])['rows']
                observed = sorted((int(row['row'][0]['Text']), row['row'][2]['Text'].strip()) for row in official_lineup)
                players = result[side]['ordered_hitters']
                assert [(p['slot'],p['name']) for p in players] == observed
                assert [p['slot'] for p in players] == list(range(1,10))
                first = {}
                for row in json.loads(box['arrHitter'][bi]['table1'])['rows']:
                    cells = row['row']
                    if len(cells) >= 3 and str(cells[0]['Text']).strip().isdigit():
                        first.setdefault(int(cells[0]['Text']), cells[2]['Text'].strip())
                for player in players:
                    assert first.get(player['slot']) == player['name']
                    pid = player['player_id']
                    assert pid not in seen
                    seen.add(pid)
                    candidates = [p for p in extract[gid][side] if p['order'] == player['slot'] and str(p['code']) == pid]
                    assert candidates
                    if player['evidence']['identity_basis'] == 'same_game_side_order_name':
                        assert any(p['name'].strip() == player['name'] for p in candidates)
                    assert official_profile_name(player['evidence']['official_profile']) == player['name']
                pitch = json.loads(box['arrPitcher'][bi]['table'])
                header = [x['Text'].strip() for x in pitch['headers'][0]['row']]
                ni, fi = header.index('선수명'), header.index('등판')
                names = [x['row'][ni]['Text'].strip() for x in pitch['rows'] if x['row'][fi]['Text'].strip() == '선발']
                starter = result[side]['starter']
                assert names == [starter['name']] and starter['actual_record_verified'] is True
                assert str(game[prefix+'_PIT_P_ID']) == starter['player_id']
                schedule_name = game[prefix+'_PIT_P_NM'].strip()
                if schedule_name != starter['name']:
                    alias = starter['name_alias_evidence']
                    assert (starter['player_id'],schedule_name,starter['name']) == ('68419','최채흥','최지명')
                    assert alias['same_game_pitcher_source_sha256'] == digest(pitcher_source)
                    archived = pitcher_games[alias['same_game_source_key']]
                    assert archived['date'] == result['date'] and archived['home'] == result['home_team'] and archived['away'] == result['away_team']
                    assert sum(str(p['pcode']) == starter['player_id'] and p['name'] == schedule_name for p in archived['data'][side]) == 1
                    assert official_profile_name(alias['official_current_profile']) == starter['name']
                else:
                    assert schedule_name == starter['name']
    assert Counter(reason for x in roster for reason in x['exclusion_reasons']) == manifest['exclusion_reasons']
    assert manifest['direct_requests_upper_bound_including_interruptions'] <= 2400
    print(json.dumps({'fixtures':len(roster), 'eligible':len(eligible), 'unique_evidence_bodies_verified':len(checked_raw)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
