import collections, hashlib, json
from pathlib import Path
p = Path('data/raw/npb_starters.json')
rows = json.loads(p.read_text(encoding='utf-8'))
ids = collections.Counter(r.get('gameId') for r in rows)
keys = sorted(set().union(*(r.keys() for r in rows)))
report = {'input': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'rows': len(rows), 'distinct_game_id_strings': len(ids), 'duplicate_game_id_strings': sum(n-1 for n in ids.values() if n>1), 'both_starter_names_nonempty': sum(bool(r.get('home_starter') and r.get('away_starter')) for r in rows), 'missing_either_starter_name': sum(not (r.get('home_starter') and r.get('away_starter')) for r in rows), 'keys': keys, 'receipt_or_official_player_id_columns': [k for k in keys if 'receiv' in k.lower() or 'player_id' in k.lower()], 'certified_receipt_count': 0, 'certified_official_player_link_count': 0, 'interpretation': 'Zero certified within this file because required fields and original documents are absent; not a count of all repository sources.'}
print(json.dumps(report,ensure_ascii=False,indent=2))
