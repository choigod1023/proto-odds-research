"""Bounded, offline inventory and streaming audit; never reads production."""
import csv
import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKTREES = HERE.parents[2]
ROOTS = [WORKTREES / 'accuracy-formula-lab/data',
         WORKTREES / 'roi-lineage-audit-20261018/data',
         WORKTREES.parent / 'sportstoto/data']
TARGETS = [('2025-04-19:MIL@IND', '02:00', '인디페이', '밀워벅스', '2025-04-19T13:00:00-04:00'),
           ('2025-04-19:DET@NYK', '07:00', '뉴욕닉스', '디트피스', '2025-04-19T18:00:00-04:00')]


def main():
    inventory, audits, matches = [], [], {t[0]: [] for t in TARGETS}
    budget = 1000000
    for root in ROOTS:
        files = sorted(root.rglob('*'))
        if len(files) > 5000:
            raise RuntimeError('Inventory exceeds 5000-entry root bound')
        candidates = [p for p in files if p.is_file() and any(s in p.name.lower()
                      for s in ('predict', 'pick', 'snapshot', 'odds_timeseries', '.db', '.sqlite'))]
        inventory.append({'root': str(root), 'exists': root.exists(),
                          'candidate_files': [str(p.relative_to(root)) for p in candidates]})
        csvs = [root / 'processed/games.csv'] + [p for p in candidates if p.name.startswith('odds_timeseries_') and p.suffix == '.csv']
        for path in csvs:
            if not path.exists():
                continue
            count, low, high, target_count = 0, None, None, 0
            truncated = False
            limit = 250000 if path.name == 'games.csv' else 5000
            with path.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                header = reader.fieldnames
                for r in reader:
                    if budget <= 0 or count >= limit:
                        truncated = True
                        break
                    count += 1
                    budget -= 1
                    ts = r.get('ts')
                    if ts:
                        low = min(low, ts) if low else ts
                        high = max(high, ts) if high else ts
                    if r.get('year') != '2025' or r.get('sport') != 'bk' or r.get('league') != 'NBA':
                        continue
                    for event, clock, home, away, start in TARGETS:
                        if ('04.20' in r.get('date_text', '') and clock in r['date_text']
                                and home in r.get('home', '') and away in r.get('away', '')):
                            target_count += 1
                            matches[event].append({'path': str(path), 'row_number_including_header': count + 1,
                                'row_sha256': hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                                'row': r, 'odds_observed_at': ts or None,
                                'pregame_price_proven': False})
            audits.append({'path': str(path), 'header': header, 'rows_read': count,
                           'complete': not truncated, 'row_limit': limit, 'ts_min_lexical': low, 'ts_max_lexical': high,
                           'exact_target_rows': target_count})
            if budget <= 0:
                break
    results = []
    for event, clock, home, away, start in TARGETS:
        kst = datetime.fromisoformat(start).astimezone(timezone(timedelta(hours=9)))
        assert kst.strftime('%Y-%m-%d %H:%M') == '2025-04-20 ' + clock
        results.append({'event_key_candidate': event, 'kickoff_kst': kst.isoformat(),
            'historical_rows': matches[event], 'historical_match_found': bool(matches[event]),
            'pregame_odds_snapshot_found_in_scanned_rows': False,
            'frozen_model_prediction_found': False, 'eligible_full_join_count': 0, 'roi': None,
            'limitations': ['historical odds lack observed_at; not proven executable pregame prices',
                'prediction status is limited to filename inventory in these three data roots; no exhaustive content search',
                'snapshot CSV scan is capped at 5000 rows per file and 1000000 rows total; absence is not exhaustive',
                'bets.csv is derived outcomes, not a frozen prediction; not used',
                'no production DB queried; no new model fit or ROI simulation performed']})
    out = {'scope': 'three explicit local data roots only', 'row_budget': 1000000,
           'rows_read': 1000000-budget, 'inventory': inventory, 'csv_audits': audits, 'events': results}
    (HERE / 'local_join_audit.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'rows_read': out['rows_read'], 'csv_files': len(audits),
                      'matches': {k: len(v) for k, v in matches.items()}, 'roi': None}))


if __name__ == '__main__':
    main()
