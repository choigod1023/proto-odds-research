import hashlib
import json
from pathlib import Path

root = Path('C:/Users/user/AppData/Local/Temp')
paths = [root / 'market-changes-audit-live-20261108.json', root / 'settlement-deploy-all-after-20261108.json']
a, b = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
histories = [rows for products in a['history'].values() for rows in products.values()]
rows = [row for history in histories for row in history]
result = {
    'inputs': [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'generated_at': obj['generated_at']} for p, obj in zip(paths, (a, b))],
    'history_rounds': len(a['history']),
    'history_product_keys': len(histories),
    'history_rows': len(rows),
    'product_keys_with_multiple_rows': sum(len(h) >= 2 for h in histories),
    'product_keys_with_changed_odds': sum(len({tuple(r.get('odds', [])) for r in h}) > 1 for h in histories),
    'observed_at_min': min(r['observed_at'] for r in rows),
    'observed_at_max': max(r['observed_at'] for r in rows),
    'result_games': len(b['result_games']),
    'result_games_with_prediction_record': sum(bool(g.get('prediction_record')) for g in b['result_games']),
    'result_games_with_options': sum(bool(g.get('options')) for g in b['result_games']),
    'past_rounds': sorted({g['round'] for g in b['past']}),
    'matched_eligible_events': None,
    'reason': 'No validated join: result rows have no options and history rows lack exact fixture identity and settlement rule; raw HTTP receipt evidence unavailable.'
}
out = Path('experiments/same-line-market-evidence-20261108/audit.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
