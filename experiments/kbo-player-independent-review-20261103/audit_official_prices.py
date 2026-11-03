"""Offline independent audit of immutable official-price source; no network or writes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

BASE = 'experiments/kbo-official-odds-20261103/'
DATA_BASE = 'experiments/kbo-player-data-20261103/'
OFFICIAL_COMMIT = '9bcbd53bf95ce6c6961b5b88fd43877152d602b6'
DATA_COMMIT = 'b0833a6'
TEAMS = {'한화':'HH', 'KT':'KT', '삼성':'SS', '두산':'OB', 'LG':'LG', '키움':'WO', 'KIA':'HT', '롯데':'LT', 'NC':'NC', 'SSG':'SK'}
KST = timezone(timedelta(hours=9))


def usable(row):
    legacy = row.get('betId') is None and row.get('betTypId') is None and row.get('handi') == 0
    modern = (row.get('betId'), row.get('betTypId'), row.get('handi'), row.get('betNm'), row.get('betTypNm')) == ('2', '2', 21, '야구 승패', '일반 승패')
    try:
        prices = [float(row['winAllot']), float(row['loseAllot'])]
        valid_prices = all(1 < x < 1000 for x in prices)
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    return bool((legacy or modern) and valid_prices and row.get('leagueCode') == 'BS001'
        and row.get('itemCode') == 'BS' and row.get('winHandi') == 0 and row.get('loseHandi') == 0
        and row.get('drawAllot') == 0 and row.get('gameSubject') in (None, '')
        and row.get('gameReject') == '0' and row.get('buyReject') == '0')


def candidates(bank, game, kickoff):
    key = (game['date'], TEAMS[game['home']], TEAMS[game['away']], f"{game['hg']}:{game['ag']}")
    return [x for x in bank.get(key, []) if kickoff is None or datetime.fromisoformat(kickoff) == x[3]]


def git_blob(repo, commit, path):
    return subprocess.check_output(['git', '-C', str(repo), 'show', commit + ':' + path])


def audit(official_repo, data_repo):
    package = subprocess.check_output(['git','-C',str(official_repo),'archive','--format=tar',OFFICIAL_COMMIT,BASE])
    with tarfile.open(fileobj=io.BytesIO(package)) as archive:
        files = {m.name[len(BASE):]: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
    manifest = json.loads(files['manifest.json'])
    assert hashlib.sha256(files['restored-prices.jsonl']).hexdigest() == manifest['output_sha256']
    output = [json.loads(s) for s in files['restored-prices.jsonl'].splitlines()]
    raw_rows = git_blob(data_repo,DATA_COMMIT,DATA_BASE+'rows.jsonl')
    raw_labels = git_blob(data_repo,DATA_COMMIT,DATA_BASE+'labels.jsonl')
    assert hashlib.sha256(raw_rows).hexdigest() == manifest['input_rows_sha256']
    assert hashlib.sha256(raw_labels).hexdigest() == manifest['input_labels_sha256']
    rows = {r['fixture_id']:r for r in map(json.loads,raw_rows.splitlines())}
    labels = {r['fixture_id']:r for r in map(json.loads,raw_labels.splitlines())}
    raw_games = (Path(data_repo)/'data/model_candidates/kbo/archived-games.jsonl.gz').read_bytes()
    assert hashlib.sha256(raw_games).hexdigest() == manifest['archived_games_sha256']
    games = {g['source_fixture_id']:g for g in map(json.loads,gzip.decompress(raw_games).splitlines())}
    identity_counts = Counter((g['date'],g['home'],g['away']) for g in games.values())
    attempts = json.loads(files['evidence/attempts.json'])
    attempts_by_file = {a['body_file']:a for a in attempts}
    assert len(attempts_by_file) == len(attempts) == 104
    rounds = json.loads(gzip.decompress(files['evidence/closed-list.body.gz']))['schedules']['data']
    rounds_by_id = {r['gmTs']:r for r in rounds}
    assert len(rounds_by_id) == len(rounds)
    bank = defaultdict(list)
    details = 0
    for filename, request in attempts_by_file.items():
        raw = gzip.decompress(files[filename])
        assert hashlib.sha256(raw).hexdigest() == request['body_sha256'] and request['http_status'] == 200
        assert datetime.fromisoformat(request['received_at']).utcoffset() is not None
        if not request['name'].startswith('detail-'):
            continue
        details += 1
        doc = json.loads(raw)
        assert request['method'] == 'POST' and request['url'].endswith('/buyPsblGame/gameInfoInq.do')
        assert request['request_json']['gmId'] == 'G101' and request['request_json']['gmTs'] == str(doc['gmTs'])
        rr = rounds_by_id[doc['gmTs']]
        assert int(request['request_json']['gameYear']) == rr['gmOsidTsYear']
        start = datetime.fromtimestamp(rr['saleStartDate']/1000,KST).date()
        end = datetime.fromtimestamp(rr['saleEndDate']/1000,KST).date()
        section = doc['compSchedules']; keys = section['keys']
        assert len(keys) == len(set(keys))
        for values in section['datas']:
            assert len(values) == len(keys)
            v = dict(zip(keys,values))
            if not usable(v):
                continue
            at = datetime.fromtimestamp(v['gameDate']/1000,KST)
            if start <= at.date() <= end:
                bank[(at.date().isoformat(),v['homeId'],v['awayId'],v['mchScore'])].append((doc['gmTs'],filename,v,at))
    assert details == manifest['successful_rounds'] == 102
    assert len(output) == len({x['fixture_id'] for x in output}) == len({x['market_id'] for x in output}) == 373
    counts = Counter()
    for out in output:
        fid = out['fixture_id']; game = games[fid]; row = rows[fid]; label = labels[fid]
        assert row['market'] is None and label['market_id'] is None
        assert (game['hg'],game['ag']) == (label['home_score'],label['away_score'])
        found = candidates(bank,game,row['kickoff_at'])
        assert len(found) == 1
        ts,filename,v,at = found[0]
        assert row['kickoff_at'] is not None or identity_counts[(game['date'],game['home'],game['away'])] == 1
        assert (out['official_round_id'],out['response_file'],out['match_seq'],out['game_at']) == (ts,filename,v['matchSeq'],at.isoformat())
        assert out['market_id'] == f"proto:{row['season']}:{rounds_by_id[ts]['gmOsidTs']}:{v['matchSeq']}:full_game_2way"
        assert out['official_home_id'] == v['homeId'] and out['official_away_id'] == v['awayId']
        assert out['score'] == v['mchScore'] and out['prices'] == {'home':v['winAllot'],'away':v['loseAllot']}
        assert out['source_market_type'] == {key:v.get(key) for key in ('betId','betTypId','handi')}
        assert out['source_reject'] == {'game':v['gameReject'],'buy':v['buyReject']}
        assert out['source_result_code'] == v['gameResult'] and out['source_status'] == v['protoStatus']
        assert out['received_at'] == attempts_by_file[filename]['received_at']
        assert datetime.fromisoformat(out['received_at']) > at
        assert out['response_sha256'] == attempts_by_file[filename]['body_sha256']
        assert out['source_observed_at'] is None and out['execution_price_verified'] is False
        assert out['availability_tier'] == 'retrospective_official_snapshot_time_unverified'
        assert out['source_kickoff_matched'] == bool(row['kickoff_at'])
        counts['clock_matched' if row['kickoff_at'] else 'clock_unverified'] += 1
        counts['draw' if game['hg'] == game['ag'] else 'decisive'] += 1
    assert sum(r['market'] is None for r in rows.values()) == manifest['missing_inputs'] == 454
    assert counts == Counter({'clock_unverified':249,'clock_matched':124,'draw':56,'decisive':317})
    return {'raw_documents':len(attempts),'detail_rounds':details,'rows':len(output),**counts,'missing_not_restored':454-len(output)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--official-repo',required=True);parser.add_argument('--data-repo',required=True);args=parser.parse_args()
    print(json.dumps(audit(args.official_repo,args.data_repo),ensure_ascii=False,sort_keys=True))
