import argparse
import gzip
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

URL = 'https://www.kleague.com/api/ddf/match/matchInfo.do'
PRICE_URL = 'https://www.betman.co.kr/buyPsblGame/gameInfoInq.do'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def price_record(row):
    kickoff = datetime.fromisoformat(row['kickoff_kst'])
    if kickoff.tzinfo is None or row['price_observed_at'] is not None:
        raise ValueError('unexpected kickoff or price provenance')
    return {'record_id': 'betman:' + str(row['gmTs']) + ':' + str(row['matchSeq']),
            'kind': 'price', 'sport': 'sc', 'league': 'K' + str(row['league_id']),
            'kickoff': kickoff.isoformat(), 'home': row['league_home_name'], 'away': row['league_away_name'],
            'source_url': PRICE_URL, 'body_sha256': row['response_sha256'],
            'received_at': row['received_at'], 'timing_basis': 'retrospective',
            'payload': {'source_fixture_id': row['fixture_id'], 'home_team_id': row['home_team_id'],
                        'away_team_id': row['away_team_id'], 'prices': row['prices'],
                        'gmId': row['gmId'], 'gmTs': row['gmTs'], 'matchSeq': row['matchSeq'],
                        'score_crosscheck': row['official_score'], 'execution_price_verified': False}}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(year, league, game, evidence):
    if year not in (2025, 2026) or league not in (1, 2) or not 1 <= game <= 999:
        raise ValueError('invalid fixture')
    evidence.mkdir(parents=True, exist_ok=True)
    stem = f'info-{year}-{league}-{game}'
    meta_path, body_path = evidence / (stem + '.json'), evidence / (stem + '.body.gz')
    if meta_path.exists() or body_path.exists():
        if not meta_path.exists() or not body_path.exists(): raise ValueError('incomplete cache')
        meta = json.loads(meta_path.read_text(encoding='utf8'))
        raw = gzip.decompress(body_path.read_bytes())
        if sha(raw) != meta['body_sha256']: raise ValueError('cache hash mismatch')
        return raw, meta, False
    params = {'year': str(year), 'meetSeq': str(league), 'gameId': str(game)}
    req = Request(URL, data=urlencode(params).encode(), headers={'User-Agent':'soccer-research/1.0', 'Referer':'https://www.kleague.com/match.do'})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        raw = response.read(1000001)
        if len(raw) > 1000000: raise ValueError('response too large')
        received = datetime.now(timezone.utc).isoformat()
        meta = {'url': URL, 'params': params, 'status': response.status, 'received_at': received, 'body_sha256': sha(raw)}
    body_path.write_bytes(gzip.compress(raw, mtime=0))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    time.sleep(1)
    return raw, meta, True

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--prices', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--spot-check', action='store_true')
    a = p.parse_args()
    with gzip.open(a.prices, 'rt', encoding='utf8') as f:
        rows = [json.loads(line) for line in f if line.strip()]
    records = [price_record(r) for r in rows]
    checks = []
    if a.spot_check:
        for year, league in ((2025,1),(2025,2),(2026,1),(2026,2)):
            row = next(r for r in rows if r['season']==year and r['league_id']==league)
            game = int(row['fixture_id'].split(':')[-1])
            raw, meta, new = fetch(year, league, game, a.evidence)
            obj = json.loads(raw); data = obj.get('data') or {}
            score = [data.get('homeGoal'), data.get('awayGoal')]
            if str(obj.get('resultCode')) != '200' or data.get('gameStatus') != 'FE' or score != row['official_score']:
                raise ValueError('official score mismatch or non-final fixture')
            record = price_record(row)
            record.update(record_id='kleague:result:'+row['fixture_id'], kind='result', source_url=URL,
                          body_sha256=meta['body_sha256'], received_at=meta['received_at'], timing_basis='retrospective',
                          payload={'source_fixture_id': row['fixture_id'], 'score': score, 'game_status':'FE'})
            records.append(record)
            checks.append({'fixture_id':row['fixture_id'], 'score':score, 'new_request':new, **meta})
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records), encoding='utf8')
    print(json.dumps({'input_prices':len(rows), 'output_records':len(records), 'spot_checks':checks, 'pregame_verified_prices':0, 'missing':'historical receipt, tipster and lineup joins not established'},ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
