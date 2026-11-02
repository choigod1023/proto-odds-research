"""Offline, conservative reconstruction from preserved bytes; never modifies inputs."""
import argparse
import base64
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import re


def read_jsonl(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_match(row):
    h = re.fullmatch(r'(.+?)\s+(\d+)', row['home'])
    a = re.fullmatch(r'(\d+)\s+(.+)', row['away'])
    t = re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})', row['date_text'])
    if not h or not a or not t:
        return None
    mo, day, hh, mm = t.groups()
    try:
        odds = [float(x) for x in row['odds'].split(',')]
    except ValueError:
        odds = []
    return {'key': (f'2025-{mo}-{day}', h[1], a[2]), 'clock': f'{hh}:{mm}',
            'scores': [int(h[2]), int(a[1])], 'odds': odds,
            'round': row['round'], 'game_no': row['game_no'], 'void': row['is_void']}


def extract_kbo(raw):
    """Read only scalar tags from inert viewstate; never instantiate .NET objects."""
    match = re.search(r'<input\b[^>]*\bid="__VIEWSTATE"[^>]*>', raw, re.I)
    if not match:
        raise ValueError('viewstate_missing')
    value = re.search(r'\bvalue="([^"]+)"', match.group())
    if not value:
        raise ValueError('viewstate_value_missing')
    decoded = base64.b64decode(value.group(1), validate=True).decode('utf8', 'replace')
    games, players = defaultdict(lambda: defaultdict(list)), defaultdict(list)
    for segment in re.findall(r'<Table1\s[^>]*>(.*?)</Table1>', decoded, re.S):
        fields = {k: html.unescape(v) for k, v in re.findall(r'<([A-Z_0-9]+)>([^<]*)</\1>', segment)}
        gid, side = fields.get('G_ID'), fields.get('TB_SC')
        if gid and side in ('T', 'B') and fields.get('G_DT') and fields.get('TEAM_ID'):
            games[gid][side].append(fields)
        elif gid and side in ('T', 'B') and fields.get('P_ID') and fields.get('SEQ_NO'):
            players[gid].append({'official_player_id': fields['P_ID'], 'side': side,
                                 'sequence': fields['SEQ_NO'], 'recorded_at_claim': fields.get('REG_DT')})
    return games, players


def dedupe_kbo(kbo_rows):
    by_fixture = defaultdict(list)
    for r in kbo_rows: by_fixture[r['fixture_id']].append(r)
    unique = []
    for group in by_fixture.values():
        group.sort(key=lambda r: (not bool(r['market']), str(r['target_event_id'])))
        row = group[0]
        prices = {tuple(r['market']['odds']) for r in group if r['market']}
        row['target_event_ids'] = [r['target_event_id'] for r in group]
        row['source_case_rows'] = len(group)
        if len(prices) > 1:
            row['market'] = None
            row['limitations'].append('conflicting_duplicate_prices')
        unique.append(row)
    return unique


def run(root, local_raw):
    research = root / 'verified-cohort-intake-20261101/experiments/verified-cohort-20261101'
    official = research / 'kleague1'
    source = root / 'link-new-source-evidence-20261030/experiments'
    kbo = source / 'source-link-baseball-20261030'
    csv_path = root / 'internal-game-factors/data/processed/games.csv.tmp'
    team_map_path = official / 'team-map.json'
    team_map = json.loads(team_map_path.read_text(encoding='utf-8-sig'))
    mapping = {k: v['domestic'] for k, v in team_map.items()}
    inputs = [team_map_path, csv_path, local_raw]
    matches, raw_lineups = defaultdict(list), defaultdict(list)
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        all_csv = list(csv.DictReader(f))
        scoped = [r for r in all_csv if r['year'] == '2025' and r['sport'] == 'sc'
                  and r['league'] == 'K리그1' and r['market_family'] == '승무패'
                  and not r['market_label'] and r['n_way'] == '3']
    malformed = 0
    for row in scoped:
        parsed = csv_match(row)
        if parsed: matches[parsed['key']].append(parsed)
        else: malformed += 1
    lineup_doc = json.loads(local_raw.read_text(encoding='utf-8-sig'))
    for gid, r in lineup_doc.items():
        if str(r.get('date', '')).startswith('2025-'):
            raw_lineups[(r['date'], r.get('home'), r.get('away'))].append((gid, r))
    rows, fixture_ids = [], set()
    for month in range(2, 12):
        path = official / f'schedule-2025-{month:02}.json'
        inputs.append(path)
        doc = json.loads(path.read_bytes())
        if str(doc['resultCode']) != '200': raise ValueError('official_schedule_failure')
        clubs = {x['teamId']: x['teamNameFull'] for x in doc['data']['clubList']}
        if any(clubs.get(k) != v['official'] for k, v in team_map.items()):
            raise ValueError('team_map_does_not_match_official')
        for f in doc['data']['scheduleList']:
            if (f['year'], f['leagueId'], f['meetSeq']) != (2025, 1, 1):
                raise ValueError('unexpected_official_competition')
            gid = f"kleague:2025:1:{f['gameId']}"
            if gid in fixture_ids: raise ValueError('duplicate_official_fixture')
            fixture_ids.add(gid)
            day = f['gameDate'].replace('.', '-')
            key = (day, mapping[f['homeTeam']], mapping[f['awayTeam']])
            markets = matches.get(key, [])
            score = [f.get('homeGoal'), f.get('awayGoal')]
            market = markets[0] if len(markets) == 1 else None
            valid_market = bool(market and market['clock'] == f.get('gameTime') and
                                market['scores'] == score and market['void'] == 'False' and
                                len(market['odds']) == 3 and all(x > 1 for x in market['odds']))
            lineup = raw_lineups.get((day, f['homeTeamName'], f['awayTeamName']), [])
            lineup = [(i, x) for i, x in lineup if [x.get('home_score'), x.get('away_score')] == score]
            players = None
            if len(lineup) == 1:
                i, x = lineup[0]
                players = {'provider_game_id': i, 'home': x['data']['home'].get('players', []),
                           'away': x['data']['away'].get('players', []),
                           'id_scope': 'provider IDs; official registry equivalence unverified',
                           'actual_pregame_receipt': None}
            reasons = []
            if not market: reasons.append('market_missing' if not markets else 'market_ambiguous')
            elif not valid_market: reasons.append('market_score_clock_void_or_price_conflict')
            if not players: reasons.append('lineup_missing_or_ambiguous')
            reasons.extend(['market_receipt_unverified', 'operating_frozen_revision_unlinked',
                            'lineup_pregame_receipt_unverified'])
            rows.append({'league':'KLEAGUE1', 'fixture_id':gid, 'day':day,
                         'kickoff_local': f.get('gameTime'), 'kickoff_timezone_verified':False,
                         'official_home_id':f['homeTeam'], 'official_away_id':f['awayTeam'],
                         'official_home':f['homeTeamName'], 'official_away':f['awayTeamName'],
                         'official_score':score, 'official_final':f.get('gameStatus') == 'FE' and f.get('endYn') == 'Y',
                         'official_source':str(path), 'market':market if valid_market else None,
                         'market_receipt_at':None, 'lineup':players,
                         'frozen_operating_revision':None, 'selection_or_hold':None,
                         'retrospective_price_result_link':bool(valid_market),
                         'prospective_verified':False, 'limitations':reasons})
    # Preserve every KBO candidate, not only fully linked cases.
    join_path, game_path = kbo/'fixture-joins.jsonl.gz', kbo/'parsed-official-games.jsonl.gz'
    inputs += [join_path, game_path]
    joins = [x for x in read_jsonl(join_path) if x.get('league') == 'KBO']
    cases_path = source/'source-link-step2-20261030/inputs/cases.jsonl.gz'
    inputs.append(cases_path)
    cases = defaultdict(list)
    for case in read_jsonl(cases_path):
        if case.get('league') == 'KBO': cases[case.get('event_id')].append(case)
    kbo_csv = defaultdict(list)
    for r in all_csv:
        if (r['year'] == '2026' and r['sport'] == 'bs' and r['league'] == 'KBO'
                and r['market_family'] == '승패' and not r['market_label'] and r['n_way'] == '2'):
            kbo_csv[(r['year'], str(int(r['round'])), str(int(r['game_no'])))].append(r)
    team_ids = {'LG':'LG', 'KT':'KT', 'KIA':'HT', 'SSG':'SK', 'NC':'NC',
                '키움':'WO', '삼성':'SS', '두산':'OB', '한화':'HH', '롯데':'LT'}
    parsed = defaultdict(list)
    for x in read_jsonl(game_path):
        if x.get('league') == 'KBO': parsed[x['game_id']].append(x)
    meta_by_hash = defaultdict(list)
    for path in (kbo/'raw').glob('*.meta.json'):
        meta = json.loads(path.read_text(encoding='utf8'))
        if meta.get('url','').endswith('/Game/SummaryView.aspx') and meta.get('status') == 200:
            meta_by_hash[meta['sha256']].append((path, meta))
    raw_cache = {}
    for j in joins:
        gid = j.get('official_game_id'); candidates = parsed.get(gid, [])
        pg = candidates[0] if len(candidates) == 1 else None
        metas = meta_by_hash.get(j.get('source_sha256'), [])
        verified = None
        if pg and len(metas) == 1:
            mp, m = metas[0]; rp = kbo / m['raw_path']
            if rp not in raw_cache:
                raw = gzip.decompress(rp.read_bytes())
                if hashlib.sha256(raw).hexdigest() != m['sha256']: raise ValueError('kbo_source_hash_mismatch')
                raw_cache[rp] = extract_kbo(raw.decode(m.get('encoding') or 'utf8', 'replace'))
                inputs += [mp, rp]
            games, people = raw_cache[rp]
            side = games.get(gid, {})
            if len(side.get('T', [])) == len(side.get('B', [])) == 1:
                a, h = side['T'][0], side['B'][0]
                if (a.get('TEAM_ID'), h.get('TEAM_ID')) == (pg.get('away_id'), pg.get('home_id')) and a.get('END_SC') == h.get('END_SC') == '1':
                    verified = {'official_home_id':h['TEAM_ID'], 'official_away_id':a['TEAM_ID'],
                                'official_score':[int(h['RUN_CN']), int(a['RUN_CN'])],
                                'official_players':people.get(gid, []), 'raw_receipt_at':m['observed_at'],
                                'raw_file':str(rp), 'raw_sha256':m['sha256']}
        market = None
        cands = cases.get(j.get('event_id'), [])
        if verified and len(cands) == 1:
            c = cands[0]
            issue = c.get('issue') or []
            found = kbo_csv.get((str(issue[0]), str(int(issue[1])), str(int(c['game_no']))), []) if len(issue) == 2 and c.get('game_no') else []
            if len(found) == 1:
                a = found[0]
                home = re.fullmatch(r'(.+?)\s+(\d+)', a['home'])
                away = re.fullmatch(r'(\d+)\s+(.+)', a['away'])
                try: odds = [float(x) for x in a['odds'].split(',')]
                except ValueError: odds = []
                if (home and away and [int(home[2]), int(away[1])] == verified['official_score']
                        and team_ids.get(home[1]) == verified['official_home_id']
                        and team_ids.get(away[2]) == verified['official_away_id']
                        and a['date_text'][:5] == j.get('kickoff','')[5:10].replace('-', '.')
                        and a['date_text'][-5:] == j.get('kickoff','')[11:16]
                        and odds == c.get('odds') and len(odds) == 2
                        and all(x > 1 for x in odds) and a['is_void'] == 'False'):
                    market = {'issue':issue, 'game_no':c['game_no'], 'odds':odds,
                              'sides':['home','away'], 'price_receipt_at':None,
                              'case_available_at_claim':c.get('available_at'),
                              'price_evidence':c.get('price_evidence'), 'source':str(csv_path)}
        reasons = ([] if verified else ['raw_official_fixture_unlinked_or_ambiguous'])
        if not market: reasons.append('market_missing_ambiguous_or_inconsistent')
        reasons += ['market_receipt_unverified', 'operating_frozen_revision_unlinked', 'pregame_receipt_unverified']
        rows.append({'league':'KBO', 'fixture_id':gid or j.get('event_id'), 'day':j.get('kickoff','')[:10],
                     'official_match':verified, 'target_event_id':j.get('event_id'),
                     'source_auxiliary':j.get('auxiliary'), 'market':market,
                     'frozen_operating_revision':None, 'prospective_verified':False,
                     'retrospective_official_fixture':bool(verified), 'limitations':reasons})
    # PR #425 includes both primary and auxiliary cases for the same game.
    # Collapse by verified physical fixture, keeping all target IDs and refusing
    # conflicting price claims rather than counting one game twice.
    kbo_rows = [r for r in rows if r['league'] == 'KBO']
    unique_kbo = dedupe_kbo(kbo_rows)
    rows = [r for r in rows if r['league'] != 'KBO'] + unique_kbo
    # The 2025 evidence is a separate cohort: preserve published-time claims
    # and postgame observations without upgrading either to pregame receipt.
    claim_root = research/'kbo'
    claim_path, claim_fixtures_path = claim_root/'evidence.jsonl', claim_root/'fixtures.jsonl'
    inputs += [claim_path, claim_fixtures_path]
    claim_fixtures = read_jsonl(claim_fixtures_path)
    claim_rows = read_jsonl(claim_path)
    raw_hashes = defaultdict(list)
    for path in (claim_root/'raw').glob('*'):
        if path.is_file():
            data = gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()
            raw_hashes[hashlib.sha256(data).hexdigest()].append(str(path))
            inputs.append(path)
    for claim in claim_rows:
        hashes = {key: raw_hashes.get(value, []) for key,value in claim.items()
                  if key.endswith('sha256') and isinstance(value,str)}
        claim['raw_hash_matches'] = hashes
        game = claim.get('game_id') or claim.get('game_id_postgame_source')
        claim['matching_fixture_evidence'] = [r for r in claim_fixtures if game and
            (r.get('game_id') == game or r.get('game_id_postgame_source') == game)]
        claim['prospective_verified'] = False
        claim['classification_note'] = 'published time is a source claim; preserved bytes were received later'
    manifest = {'inputs':[{'path':str(p), 'sha256':sha(p)} for p in sorted(set(inputs),key=str)],
                'k_league':{'official_fixture_denominator':len(fixture_ids), 'raw_market_rows':len(scoped),
                            'malformed_market_rows':malformed,
                            'duplicate_market_keys':sum(len(x)-1 for x in matches.values() if len(x)>1),
                            'retrospective_price_result':sum(r.get('retrospective_price_result_link',False) for r in rows),
                            'lineups':sum(bool(r.get('lineup')) for r in rows if r['league']=='KLEAGUE1')},
                'kbo':{'target_rows':len(joins), 'unique_official_fixtures':len(unique_kbo),
                       'duplicate_physical_fixture_rows':len(kbo_rows)-len(unique_kbo),
                       'verified_official_fixture':sum(bool(r.get('retrospective_official_fixture')) for r in rows),
                       'duplicate_target_ids':sum(v-1 for v in Counter(j.get('event_id') for j in joins).values() if v>1),
                       'retrospective_price_result':sum(bool(r.get('market')) for r in rows if r['league']=='KBO')},
                'reason_counts_nonexclusive':dict(Counter(reason for row in rows for reason in row['limitations'])),
                'kbo_2025_separate_evidence':{'rows':len(claim_rows),
                    'classification_counts':dict(Counter(r.get('classification') for r in claim_rows)),
                    'fixture_rows':len(claim_fixtures)},
                'prospective_full_cohort':sum(r['prospective_verified'] for r in rows),
                'hash_scope':'byte integrity only; not proof of historical availability'}
    return rows, manifest, claim_rows


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--worktrees', type=Path, required=True)
    p.add_argument('--lineup-raw', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args=p.parse_args()
    rows, manifest, claims=run(args.worktrees,args.lineup_raw)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'cohort.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf8')
    (args.out/'kbo-2025-evidence.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in claims),encoding='utf8')
    (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in manifest.items() if k!='inputs'},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
