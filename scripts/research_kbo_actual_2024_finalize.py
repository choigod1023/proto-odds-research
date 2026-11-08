"""Resolve official 2024 lineups to retrospective IDs and package evidence."""
from collections import Counter
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tarfile
from datetime import datetime

import research_kbo_actual_2024_capture as capture

STATE = capture.STATE
OUT = Path(__file__).resolve().parents[1] / 'experiments/kbo-actual-2024-20261103'
BATTERS = capture.BASE / 'data/raw/detail/kbo_batters_indiv_2023_2026.json'
BATTERS_SHA = '24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'
PITCHERS = capture.BASE / 'data/raw/detail/kbo_baseball_2023_2026.json'
PITCHERS_SHA = 'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'


class ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == 'span' and dict(attrs).get('id', '').endswith('playerProfile_lblName'):
            self.inside = True

    def handle_data(self, data):
        if self.inside:
            self.names.append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'span':
            self.inside = False


def profile_name(raw):
    p = ProfileParser()
    p.feed(raw.decode('utf8'))
    if len(p.names) != 1 or not p.names[0]:
        raise ValueError('profile_name_missing_or_ambiguous')
    return p.names[0]


def raw(meta):
    data = gzip.decompress((STATE / 'raw' / (meta['sha256'] + '.gz')).read_bytes())
    if capture.sha(data) != meta['sha256']:
        raise ValueError('evidence_hash_mismatch')
    return data


def parse_roster(lineup, box, game, alias_evidence=None):
    result = {}
    for side, li, bi, prefix in [('away', 4, 0, 'T'), ('home', 3, 1, 'B')]:
        ordered = []
        for item in json.loads(lineup[li][0])['rows']:
            cells = item['row']
            ordered.append({'slot':int(cells[0]['Text']), 'name':cells[2]['Text'].strip()})
        if sorted(x['slot'] for x in ordered) != list(range(1, 10)) or len({x['name'] for x in ordered}) != 9:
            raise ValueError(side + ':invalid_confirmed_lineup')
        ordered.sort(key=lambda x:x['slot'])
        # Official boxscore is only a cross-check of the independent confirmed lineup.
        # A first-in-slot row by itself is never promoted to a starting lineup.
        first = {}
        for item in json.loads(box['arrHitter'][bi]['table1'])['rows']:
            cells = item['row']
            if len(cells) >= 3 and str(cells[0]['Text']).strip().isdigit():
                slot = int(cells[0]['Text'])
                if 1 <= slot <= 9 and slot not in first:
                    first[slot] = cells[2]['Text'].strip()
        if any(first.get(x['slot']) != x['name'] for x in ordered):
            raise ValueError(side + ':confirmed_lineup_actual_boxscore_mismatch')
        table = json.loads(box['arrPitcher'][bi]['table'])
        headers = [x['Text'].strip() for x in table['headers'][0]['row']]
        if headers.count('선수명') != 1 or headers.count('등판') != 1:
            raise ValueError(side + ':pitcher_header_missing')
        name_idx, flag_idx = headers.index('선수명'), headers.index('등판')
        starters = [x['row'][name_idx]['Text'].strip() for x in table['rows']
                    if x['row'][flag_idx]['Text'].strip() == '선발']
        if len(starters) != 1:
            raise ValueError(side + ':actual_starter_not_unique')
        pid, official_name = str(game.get(prefix + '_PIT_P_ID') or ''), str(game.get(prefix + '_PIT_P_NM') or '').strip()
        alias = (alias_evidence or {}).get((side,pid,official_name,starters[0]))
        if not pid.isdigit() or (official_name != starters[0] and alias is None):
            raise ValueError(side + ':schedule_actual_starter_identity_mismatch')
        result[side] = {'ordered_hitters':ordered, 'starter':{'player_id':pid, 'name':starters[0],
                        'actual_record_verified':True, 'evidence':'official_schedule_starting_pitcher_id_and_name_exact_match_official_boxscore_start_flag'}}
        if alias is not None:
            result[side]['starter']['schedule_name'] = official_name
            result[side]['starter']['evidence'] = 'official_schedule_id_and_same_game_archived_id_and_official_current_profile; explicit_boxscore_start_flag'
            result[side]['starter']['name_alias_evidence'] = alias
    return result


def candidate_ids(players, participants):
    for player in players:
        same_order = [x for x in participants if x.get('order') == player['slot'] and str(x.get('code','')).isdigit()]
        exact = sorted({str(x['code']) for x in same_order if str(x.get('name','')).strip() == player['name']})
        player['_candidate_ids'] = exact if exact else sorted({str(x['code']) for x in same_order})
        player['_candidate_basis'] = 'same_game_side_order_name' if exact else 'same_game_side_order_profile_name_required'


def old_profile_cache():
    profiles = {}
    folders = [Path('C:/Users/user/Documents/Codex/klu/.research-state/kbo-lineups-v5'),
               Path('C:/Users/user/Documents/Codex/klu/.research-state/kbo-lineups-v3'),
               Path('C:/Users/user/Documents/Codex/kbor/.research-state/kbo-daily-cookiefree-v3')]
    for root in folders:
        for f in sorted((root / 'requests').glob('*.json')):
            m = json.loads(f.read_text(encoding='utf8'))
            pid = str((m.get('params') or m.get('query') or {}).get('playerId') or '')
            if not pid.isdigit() or m.get('http_status') != 200 or '/Player/HitterDetail/' not in m.get('url',''):
                continue
            path = root / 'raw' / m['sha256']
            if not path.exists():
                path = path.with_suffix('.gz')
            if not path.exists():
                continue
            body = path.read_bytes()
            if path.suffix == '.gz':
                body = gzip.decompress(body)
            if capture.sha(body) != m['sha256']:
                raise ValueError('old_profile_cache_hash_mismatch')
            try:
                name = profile_name(body)
            except (ValueError, UnicodeError):
                continue
            if pid in profiles and profiles[pid][0] != name:
                # A changed official name needs explicit resolution; never silently choose.
                profiles[pid] = (None, None)
                continue
            dest = STATE / 'raw' / (m['sha256'] + '.gz')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(gzip.compress(body, mtime=0))
            profiles[pid] = (name, m)
    return profiles


def _main():
    source = BATTERS.read_bytes()
    if capture.sha(source) != BATTERS_SHA:
        raise ValueError('batter_source_hash_mismatch')
    batters = json.loads(source)
    pitcher_bytes = PITCHERS.read_bytes()
    if capture.sha(pitcher_bytes) != PITCHERS_SHA:
        raise ValueError('pitcher_source_hash_mismatch')
    pitchers = json.loads(pitcher_bytes)
    population = capture.rows()
    interim = []
    needed = Counter()
    identity_extract = {}
    for row in population:
        fid = row['fixture_id']
        p = STATE / 'games' / (capture.sha(fid.encode()) + '.json')
        if not p.exists():
            raise RuntimeError('capture_incomplete:' + fid)
        evidence = json.loads(p.read_text(encoding='utf8'))
        if evidence.get('capture_version') != 2:
            raise RuntimeError('capture_version_incomplete:' + fid)
        day = capture.game_day(row)
        record = {'fixture_id':fid, 'season':2024, 'date':day[:4] + '-' + day[4:6] + '-' + day[6:],
                  'home_team':row['home_id'], 'away_team':row['away_id'],
                  'official_game_id':evidence.get('official_game_id'), 'exclusion_reasons':evidence.get('exclusion_reasons', []),
                  'historical_publication_time':None, 'historical_receipt_time':None,
                  'eligible_as_pregame_feature':False, 'identity_tier':'retrospective_only'}
        if not record['exclusion_reasons']:
            try:
                game = evidence['official_game']
                alias_evidence = {}
                # Only this exact officially documented player rename is allowed;
                # no fuzzy or guessed names. Archive is used solely for identity.
                if str(game.get('B_PIT_P_ID')) == '68419' and str(game.get('B_PIT_P_NM','')).strip() == '최채흥':
                    pitcher_key = game['G_ID'] + '2024'
                    pitcher_game = pitchers.get(pitcher_key) or pitchers.get(fid)
                    if pitcher_game and pitcher_game.get('gameId') in (pitcher_key, fid) and pitcher_game.get('date','').replace('-','') == day and pitcher_game.get('home') == row['home_id'] and pitcher_game.get('away') == row['away_id']:
                        matches = [x for x in pitcher_game['data']['home'] if str(x.get('pcode')) == '68419' and x.get('name') == '최채흥']
                        if len(matches) == 1:
                            profile, profile_meta = capture.fetch('/Record/Player/PitcherDetail/Basic.aspx', {'playerId':'68419'}, method='GET', as_json=False)
                            if profile_name(profile) == '최지명':
                                alias_evidence[('home','68419','최채흥','최지명')] = {'same_game_archived_player_id':'68419', 'same_game_archived_name':'최채흥',
                                    'same_game_pitcher_source_sha256':PITCHERS_SHA, 'same_game_source_key':pitcher_game['gameId'],
                                    'official_current_profile':profile_meta, 'official_identity_audit_commit':'ffe52635f64b51b1b45e59202c4db99b0a528a5d'}
                parsed = parse_roster(json.loads(raw(evidence['lineup_evidence'])), json.loads(raw(evidence['boxscore_evidence'])), game, alias_evidence)
                source_key = game['G_ID'] + '2024'
                source_game = batters.get(source_key)
                if not source_game and fid[:4] != '2024' and not fid.startswith('score:'):
                    # The archived fixture's synthetic year is bound to the official
                    # game by the unique dated team pairing checked during capture.
                    source_key = fid
                    source_game = batters.get(source_key)
                if not source_game:
                    raise ValueError('same_game_batter_identity_source_missing')
                if (str(source_game.get('gameId')) != source_key or
                    str(source_game.get('date','')).replace('-', '') != day or
                    source_game.get('home') != row['home_id'] or source_game.get('away') != row['away_id']):
                    raise ValueError('batter_identity_source_game_team_date_mismatch')
                safe_extract = {}
                for side in ('home','away'):
                    participants = source_game['data'][side]
                    safe_extract[side] = [{k:x.get(k) for k in ('code','name','order')} for x in participants]
                    candidate_ids(parsed[side]['ordered_hitters'], participants)
                    for player in parsed[side]['ordered_hitters']:
                        needed.update(player['_candidate_ids'])
                identity_extract[game['G_ID']] = safe_extract
                record.update(parsed)
                record['batter_identity_source_key'] = source_key
                record['fixture_match_basis'] = ('unique_official_schedule_date_and_team_pair' if fid.startswith('score:') else 'synthetic_year_official_schedule_date_team_codes_header' if fid[:4] != '2024' else 'exact_official_game_id_and_schedule_team_pair')
                record['official_series_id'] = str(game['SR_ID'])
                record['official_home_team_id'] = game['HOME_ID']
                record['official_away_team_id'] = game['AWAY_ID']
                record['lineup_semantics'] = 'confirmed_official_lineup_crosschecked_against_official_game_record; mismatches_excluded'
                record['source_kind'] = 'official_confirmed_lineup_and_boxscore_with_official_schedule_and_profile_identity'
                record['evidence'] = {k:evidence[k] for k in ('schedule_evidence','lineup_evidence','boxscore_evidence')}
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                record['exclusion_reasons'].append(str(exc))
        interim.append(record)
    profiles = old_profile_cache()
    for i, (pid, _) in enumerate(needed.most_common()):
        if pid in profiles:
            continue
        body, meta = capture.fetch('/Record/Player/HitterDetail/Basic.aspx', {'playerId':pid}, method='GET', as_json=False)
        try:
            profiles[pid] = (profile_name(body), meta)
        except (ValueError, UnicodeError):
            profiles[pid] = (None, meta)
        capture.progress(stage='profile_capture', completed=i + 1, total=len(needed), player_id=pid)
    for record in interim:
        if record['exclusion_reasons']:
            continue
        seen = set()
        for side in ('home','away'):
            for player in record[side]['ordered_hitters']:
                candidates = player.pop('_candidate_ids')
                basis = player.pop('_candidate_basis')
                hits = [pid for pid in candidates if profiles.get(pid, (None,))[0] == player['name']]
                if len(hits) != 1 or hits[0] in seen:
                    record['exclusion_reasons'].append(side + ':ambiguous_or_missing_official_player_id:slot_' + str(player['slot']))
                    continue
                pid = hits[0]
                seen.add(pid)
                player['player_id'] = pid
                player['evidence'] = {'identity_basis':basis, 'same_game_batter_source_sha256':BATTERS_SHA, 'official_profile':profiles[pid][1]}
    game_ids = Counter(x['official_game_id'] for x in interim if x['official_game_id'])
    for record in interim:
        if record['official_game_id'] and game_ids[record['official_game_id']] > 1:
            record['exclusion_reasons'].append('official_game_linked_to_multiple_fixtures')
    OUT.mkdir(parents=True, exist_ok=True)
    roster = b''.join((json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n').encode('utf8') for x in interim)
    (OUT / 'rosters.jsonl').write_bytes(roster)
    (OUT / 'batter-identity-only.json').write_text(json.dumps(identity_extract, ensure_ascii=False, sort_keys=True), encoding='utf8')
    # Include exact raw responses with current receipt metadata; gzip also contains target game
    # statistics and is evidence only, never an allowed feature source.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        files = sorted((STATE / 'requests').glob('*.json')) + sorted((STATE / 'raw').glob('*.gz'))
        files += [STATE / 'attempts.jsonl']
        for path in files:
            data = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(STATE).as_posix())
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    (OUT / 'official-evidence.tar.gz').write_bytes(gzip.compress(buf.getvalue(), mtime=0))
    counts = Counter(reason for x in interim for reason in x['exclusion_reasons'])
    attempts = [json.loads(line) for line in (STATE/'attempts.jsonl').read_text(encoding='utf8').splitlines()]
    starts = sorted(datetime.fromisoformat(a['started_at']) for a in attempts)
    intervals = [(b-a).total_seconds() for a,b in zip(starts,starts[1:])]
    request_keys = Counter((a['url'], json.dumps({k:str(v) for k,v in a['params'].items()}, sort_keys=True)) for a in attempts)
    manifest = {'season':2024, 'population':len(interim), 'eligible':sum(not x['exclusion_reasons'] for x in interim),
                'exclusion_reasons':dict(counts), 'input_sha256':capture.EXPECTED, 'batter_source_sha256':BATTERS_SHA,
                'rosters_sha256':capture.sha(roster), 'new_direct_requests':sum(1 for _ in (STATE/'attempts.jsonl').open(encoding='utf8')),
                'batter_identity_extract_sha256':capture.sha((OUT/'batter-identity-only.json').read_bytes()),
                'pitcher_identity_source_sha256':PITCHERS_SHA,
                'possible_additional_interrupted_requests_upper_bound':6,
                'direct_requests_upper_bound_including_interruptions':len(attempts) + 6,
                'request_spacing_violations_under_1_5_seconds':sum(x < 1.5 for x in intervals),
                'minimum_recorded_request_start_interval_seconds':min(intervals) if intervals else None,
                'repeated_request_attempts':sum(n-1 for n in request_keys.values() if n > 1),
                'request_spacing_note':'Earlier restart left overlapping collection processes; both were stopped, evidence hashes checked, and an exclusive lock added.',
                'contract_sha256':'512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480',
                'historical_receipt_proven':False, 'feature_policy':'identity_only; do not use target-game statistics or outcomes',
                'evidence_bundle_sha256':capture.sha((OUT/'official-evidence.tar.gz').read_bytes())}
    capture.save(OUT/'manifest.json', manifest)
    capture.progress(stage='finalized', **manifest)
    print(json.dumps(manifest, ensure_ascii=False))


def main():
    with capture.capture_lock():
        _main()


if __name__ == '__main__':
    main()
