"""Fetch bounded official round snapshots and conservatively link missing KBO prices."""
import gzip
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = Path(r'C:\Users\user\Documents\Codex\kbodata')
SOURCE = DATA / 'experiments/kbo-player-data-20261103'
GAME_PATH = DATA / 'data/model_candidates/kbo/archived-games.jsonl.gz'
KST = timezone(timedelta(hours=9))
BASE = 'https://www.betman.co.kr'
MAX_REQUESTS = 110
TEAMS = {'한화': 'HH', 'KT': 'KT', '삼성': 'SS', '두산': 'OB', 'LG': 'LG',
         '키움': 'WO', 'KIA': 'HT', '롯데': 'LT', 'NC': 'NC', 'SSG': 'SK'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path):
    with path.open(encoding='utf8') as f:
        return [json.loads(line) for line in f]


def eligible_rounds(day, season, rounds):
    return [x for x in rounds if x.get('gmOsidTsYear') == season
            and x.get('saleStartDate') and x.get('saleEndDate')
            and datetime.fromtimestamp(x['saleStartDate']/1000, KST).date() <= day
            <= datetime.fromtimestamp(x['saleEndDate']/1000, KST).date()]


def match(row, game, candidate):
    """Require league, market shape, official IDs, date, score, and known clock."""
    if candidate.get('leagueCode') != 'BS001':
        return False
    # Older responses lack market IDs; newer responses identify baseball
    # full-game winner explicitly, with handi=21 (a type code, not a line).
    modern = candidate.get('betId') == '2' and candidate.get('betTypId') == '2'
    legacy = candidate.get('betId') is None and candidate.get('betTypId') is None and candidate.get('handi') == 0
    if not (modern or legacy):
        return False
    if candidate.get('homeId') != TEAMS.get(game['home']) or candidate.get('awayId') != TEAMS.get(game['away']):
        return False
    if candidate.get('drawAllot') not in (0, 0.0) or candidate.get('gameSubject') not in (None, ''):
        return False
    if candidate.get('gameReject') != '0' or candidate.get('buyReject') != '0':
        return False
    if candidate.get('mchScore') != f"{game['hg']}:{game['ag']}":
        return False
    try:
        start = datetime.fromtimestamp(candidate['gameDate']/1000, KST)
        home, away = float(candidate['winAllot']), float(candidate['loseAllot'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not (1 < home < 1000 and 1 < away < 1000) or start.date().isoformat() != game['date']:
        return False
    if row.get('kickoff_at') and datetime.fromisoformat(row['kickoff_at']) != start:
        return False
    return True


def main():
    rows_bytes = (SOURCE / 'rows.jsonl').read_bytes()
    labels_bytes = (SOURCE / 'labels.jsonl').read_bytes()
    assert sha(rows_bytes) == '66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c'
    assert sha(labels_bytes) == 'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca'
    missing = [r for r in read_jsonl(SOURCE/'rows.jsonl') if r['market'] is None]
    with gzip.open(GAME_PATH, 'rt', encoding='utf8') as f:
        games = {g['source_fixture_id']: g for g in map(json.loads, f)}
    rounds = json.loads(gzip.decompress((ROOT/'evidence/closed-list.body.gz').read_bytes()))['schedules']['data']
    plan, per_fixture = {}, {}
    for row in missing:
        g = games[row['fixture_id']]
        candidates = eligible_rounds(date.fromisoformat(g['date']), row['season'], rounds)
        per_fixture[row['fixture_id']] = [x['gmTs'] for x in candidates]
        for x in candidates:
            plan[x['gmTs']] = x
    if len(plan) > MAX_REQUESTS:
        raise ValueError(f'round cap exceeded: {len(plan)} > {MAX_REQUESTS}')
    attempts_path = ROOT/'evidence/attempts.json'
    attempts = json.loads(attempts_path.read_text(encoding='utf8'))
    if any(a.get('http_status') in (403, 429) for a in attempts):
        raise RuntimeError('Previous 403/429 recorded; do not automatically retry or continue requests')
    snapshots = {}
    session = requests.Session()
    for ts, x in sorted(plan.items()):
        y, rd = x['gmOsidTsYear'], x['gmOsidTs']
        name = f'detail-{y}-{rd}'
        body_path = ROOT/'evidence'/f'{name}.body.gz'
        original = ROOT/'evidence'/f'{name}.body'
        previous = next((a for a in attempts if a['name'] == name and a.get('http_status') == 200), None)
        if previous and original.exists():
            raw = original.read_bytes()
            body_path.write_bytes(gzip.compress(raw, mtime=0))
            original.unlink()
            previous['body_file'] = f'evidence/{name}.body.gz'
            previous['body_encoding'] = 'gzip; sha256 is of decompressed response bytes'
        elif previous and body_path.exists():
            raw = gzip.decompress(body_path.read_bytes())
        else:
            payload = {'gmId':'G101','gmTs':str(ts),'gameYear':str(y),'_sbmInfo':{'debugMode':'false'}}
            record = {'name':name,'method':'POST','url':BASE+'/buyPsblGame/gameInfoInq.do',
                      'request_json':payload,'timeout_seconds':15}
            try:
                response = session.post(record['url'],json=payload,timeout=15,
                    headers={'User-Agent':'Mozilla/5.0','X-Requested-With':'XMLHttpRequest',
                             'Referer':BASE+'/main/mainPage/gamebuy/closedGameList.do'})
                raw = response.content
                record.update(received_at=datetime.now(timezone.utc).isoformat(),http_status=response.status_code,
                              content_type=response.headers.get('Content-Type'),body_sha256=sha(raw),
                              body_file=f'evidence/{name}.body.gz',body_encoding='gzip; sha256 is of decompressed response bytes')
                body_path.write_bytes(gzip.compress(raw,mtime=0))
                attempts.append(record)
                attempts_path.write_text(json.dumps(attempts,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                if response.status_code in (403,429):
                    break
                if response.status_code != 200:
                    continue
            except requests.RequestException as exc:
                attempts.append(record | {'failed_at':datetime.now(timezone.utc).isoformat(),
                                          'http_status':None,'error_type':type(exc).__name__,'error':str(exc)})
                attempts_path.write_text(json.dumps(attempts,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                time.sleep(1)
                continue
            time.sleep(1)
        if sha(raw) != next(a['body_sha256'] for a in reversed(attempts) if a['name']==name and a.get('http_status')==200):
            raise ValueError('response hash mismatch '+name)
        data = json.loads(raw)
        if data.get('gmTs') != ts:
            raise ValueError('round mismatch '+name)
        c = data.get('compSchedules') or {}
        snapshots[ts] = (name, [dict(zip(c['keys'], values)) for values in c['datas']])
    attempts_path.write_text(json.dumps(attempts,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    by_game = Counter((g['date'],g['home'],g['away']) for g in games.values())
    output, reasons = [], Counter()
    for row in missing:
        g=games[row['fixture_id']]; candidates=[]
        if not row['kickoff_at'] and by_game[(g['date'],g['home'],g['away'])] > 1:
            reasons['doubleheader_without_clock'] += 1; continue
        if not per_fixture[row['fixture_id']]:
            reasons['no_round_in_sale_window'] += 1; continue
        for ts in per_fixture[row['fixture_id']]:
            if ts in snapshots:
                name, market_rows = snapshots[ts]
                candidates.extend((ts,name,v) for v in market_rows if match(row,g,v))
        if len(candidates) != 1:
            reasons['ambiguous' if candidates else 'no_verified_match'] += 1; continue
        ts,name,v=candidates[0]
        source=next(a for a in reversed(attempts) if a['name']==name and a.get('http_status')==200)
        output.append({'fixture_id':row['fixture_id'],'market_id':f"proto:{row['season']}:{plan[ts]['gmOsidTs']}:{v['matchSeq']}:full_game_2way",
            'official_round_id':ts,'match_seq':v['matchSeq'],'official_home_id':v['homeId'],'official_away_id':v['awayId'],
            'game_at':datetime.fromtimestamp(v['gameDate']/1000,KST).isoformat(),'score':v['mchScore'],
            'prices':{'home':v['winAllot'],'away':v['loseAllot']},'draw_allot':v['drawAllot'],
            'source_result_code':v['gameResult'],'source_status':v['protoStatus'],
            'source_market_type':{'betId':v.get('betId'),'betTypId':v.get('betTypId'),'handi':v.get('handi')},
            'source_reject':{'game':v['gameReject'],'buy':v['buyReject']},
            'source_observed_at':None,'received_at':source['received_at'],'response_sha256':source['body_sha256'],
            'response_file':source['body_file'],'execution_price_verified':False,
            'source_kickoff_matched':bool(row['kickoff_at']),
            'availability_tier':'retrospective_official_snapshot_time_unverified'})
    dupes=Counter(x['market_id'] for x in output)
    output=[x for x in output if dupes[x['market_id']]==1]
    # This frozen artifact was published with CRLF.  Write its exact bytes on
    # every platform; the manifest and downstream analyses pin this SHA-256.
    payload=''.join(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\r\n' for x in output)
    (ROOT/'restored-prices.jsonl').write_bytes(payload.encode('utf8'))
    manifest={'status':'ready_for_join' if output else 'blocked_with_actual_response_evidence',
        'input_rows_sha256':sha(rows_bytes),'input_labels_sha256':sha(labels_bytes),
        'archived_games_sha256':sha(GAME_PATH.read_bytes()),'missing_inputs':len(missing),
        'planned_rounds':len(plan),'successful_rounds':len(snapshots),'restored':len(output),
        'restored_draws':sum(x['score'].split(':')[0]==x['score'].split(':')[1] for x in output),
        'restored_without_source_kickoff':sum(not x['source_kickoff_matched'] for x in output),
        'excluded':dict(reasons),'duplicate_market_rows_removed':sum(v for v in dupes.values() if v>1),
        'output_sha256':sha((ROOT/'restored-prices.jsonl').read_bytes()),
        'limitation':'Retrospective official fields; no historical observation or execution timestamp verified.'}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
