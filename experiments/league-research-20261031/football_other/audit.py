"""Inventory named archived 1X2 offers; observation is not receipt certification."""
import csv
import datetime as dt
import glob
import hashlib
import json
import pathlib
import re
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[3]
LEAGUES = ('UCL', 'UEL', '한국FA컵', '코파리베', '잉리그컵')
groups = defaultdict(list)
hashes = {}
for name in sorted(glob.glob(str(ROOT / 'data/raw/snapshots/odds_timeseries_*.csv'))):
    path = pathlib.Path(name)
    hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row.get('sport') == 'sc' and row.get('league') in LEAGUES and row.get('market_family') == '승무패' and row.get('n_way') == '3':
                groups[(row['league'], row['year'], row['round'], row['game_no'])].append(row)

out = {league: defaultdict(int) for league in LEAGUES}
for (league, year, rnd, game_no), rows in groups.items():
    c = out[league]
    c['offer_ids'] += 1
    valid_price, pre_t30, results, early_label = False, False, set(), False
    for row in rows:
        try:
            odds = [float(x) for x in row['odds'].split(',')]
            price_ok = len(odds) == 3 and all(x > 1 for x in odds)
        except (ValueError, KeyError):
            price_ok = False
        valid_price |= price_ok
        if row.get('result', '').strip() in {'홈승', '무승부', '홈패'}:
            results.add(row['result'].strip())
        m = re.match(r'(\d\d)\.(\d\d)\([^)]*\)\s+(\d\d):(\d\d)', row.get('date_text', ''))
        if m:
            try:
                kickoff = dt.datetime(int(year), *map(int, m.groups()), tzinfo=dt.timezone(dt.timedelta(hours=9)))
                observed = dt.datetime.fromisoformat(row['ts'])
                pre_t30 |= price_ok and observed <= kickoff - dt.timedelta(minutes=30)
                early_label |= row.get('result', '').strip() in {'홈승', '무승부', '홈패'} and observed < kickoff
            except (ValueError, TypeError):
                pass
    c['any_three_prices'] += valid_price
    c['price_timestamp_before_t30_uncertified'] += pre_t30
    c['recognized_result_label'] += bool(results)
    c['conflicting_recognized_result_labels'] += len(results) > 1
    c['recognized_result_before_kickoff'] += early_label
    c['actual_receipt_certified'] += 0
    c['official_confirmed_player_linked'] += 0

payload = {'source': 'tracked csv snapshots only', 'identity': ['league', 'year', 'round', 'game_no'],
           'caveats': ['offer IDs not independently verified unique fixtures', 'timestamp is stored ts, not proven receipt; date parsed as KST using listed year', 'recognized result label is not official settlement', 'league aliases with mojibake not merged', 'no source document bytes or player links in these rows'],
           'snapshot_sha256s': hashes, 'counts': out}
print(json.dumps(payload, ensure_ascii=False, indent=2))
