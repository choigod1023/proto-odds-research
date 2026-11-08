"""Inventory bounded source evidence and explicit single-game price joins."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

from extract import EVIDENCE, FIXTURES, extract

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRICE = ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
REV = '2df626042f633ba08f381f755ccc18f2068733f6'
OFFICIAL = 'experiments/nba-official-information-20261103/'


def blob(path):
    return subprocess.check_output(['git', 'show', REV+':'+OFFICIAL+path], cwd=ROOT)


def unique_price(rows, league, day, home, away, winner):
    matches = [r for r in rows if (r.get('league'),r.get('day'),r.get('home'),r.get('away')) ==
               (league,day,home,away)]
    if len(matches) != 1 or matches[0].get('y') != winner:
        raise ValueError('missing, ambiguous or inconsistent price join')
    q = matches[0]
    return {'count': 1, 'day': day, 'odds': q['odds'], 'winner_index': q['y'],
            'kickoff_precision': q['kickoff_precision'], 'limitations':
            ['no product id', 'no settlement period or overtime rule', 'no actual price receipt time']}


def main():
    meta = json.loads((EVIDENCE/'kbl-box-S45G01N116.json').read_text(encoding='utf-8'))
    body = gzip.decompress((EVIDENCE/'kbl-box-S45G01N116.body.gz').read_bytes())
    fixtures = [json.loads(s) for s in FIXTURES.read_text(encoding='utf-8').splitlines()]
    kbl = extract(meta, body, fixtures)
    manifest = json.loads(blob('manifest.json'))
    entries = [r for r in manifest['requests'] if r.get('file') == 'raw/box-0022500274.html']
    if len(entries) != 1:
        raise ValueError('missing NBA archive receipt')
    nba_body, nba_meta = blob('raw/box-0022500274.html'), entries[0]
    digest = hashlib.sha256(nba_body).hexdigest()
    if digest != nba_meta['sha256']:
        raise ValueError('NBA archive hash mismatch')
    match = re.search(rb'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', nba_body)
    if not match:
        raise ValueError('NBA box embedded JSON missing')
    nba = json.loads(match.group(1))['props']['pageProps']['game']
    if nba['gameId'] != '0022500274' or nba['gameStatus'] != 3:
        raise ValueError('wrong NBA final game')
    home, away = nba['homeTeam'], nba['awayTeam']
    if (home['teamTricode'], away['teamTricode'], home['score'], away['score']) != ('DEN','SAC',123,128):
        raise ValueError('NBA game identity or score mismatch')
    with gzip.open(PRICE, 'rt', encoding='utf-8') as f:
        prices = [json.loads(line) for line in f]
    labels = ['nba-cdn-0022500274','nba-stats-0022500274','kbl-box-S45G01N116',
              'wkbl-04601072','nba-injury-20261104']
    receipts = [json.loads((EVIDENCE/(label+'.json')).read_text(encoding='utf-8')) for label in labels]
    receipts = [{k:r.get(k) for k in ('label','url','final_url','status','bytes','body_sha256','received_at','timestamp_basis','error')} for r in receipts]
    result = {'receipts': receipts, 'kbl': {'game_id': kbl['source_game_id'], 'kickoff': kbl['fixture_kickoff'],
              'team_totals': kbl['team_totals'], 'player_rows': len(kbl['players']),
              'price_join': unique_price(prices,'KBL','2025-01-01','KT소닉붐','부산KCC',0)},
              'nba_archived_official': {'source_url': nba_meta['url'], 'body_sha256': digest,
              'received_at': nba_meta['received_at'], 'game_id': nba['gameId'], 'kickoff_utc': nba['gameTimeUTC'],
              'teams': {x['teamTricode']: {'team_id':x['teamId'], 'score':x['score'], 'player_rows':len(x['players']),
                        'starter_count':sum(bool(p.get('position')) for p in x['players'])} for x in (home,away)},
              'price_join': unique_price(prices,'NBA','2025-11-23','덴버너게','새크킹스',1)},
              'wkbl_price_candidate': unique_price(prices,'WKBL','2026-02-15','우리은행','삼성생명',1),
              'wkbl_note': 'Official page 403 in local capture; search index result is not a retained source body. Price link is provisional.',
              'model_eligible_games': 0, 'model_eligible_note': 'No predecision 10-game received official input history and exact timed product and settlement.'}
    (HERE/'source-summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
