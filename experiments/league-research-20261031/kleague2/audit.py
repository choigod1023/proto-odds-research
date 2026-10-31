import gzip, hashlib, importlib.util, json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CON = ROOT / 'experiments/accuracy-connection-20261030'
TARGET = ROOT / 'experiments/accuracy-review-20261030/inputs/targets.jsonl.gz'

def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def eligible(received, decision, kickoff):
    return received <= decision <= kickoff - timedelta(minutes=30)

def training_labels(rows, cutoff):
    return [r for r in rows if r['available_at'] < cutoff]

def unique_docs(rows):
    seen = set()
    result = []
    for row in rows:
        key = (row['event_id'], row['sha256'])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result

def audit():
    official = load(CON / 'official-2025.json')
    prior = load(CON / 'prior-2025.json')
    spec = importlib.util.spec_from_file_location('connection_match', CON / 'match.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    linked = mod.link(prior, official)
    targets = [json.loads(line) for line in gzip.open(TARGET, 'rt', encoding='utf-8')]
    keys = {(r['league'], r['kickoff_at'][:10], r['home'], r['away']) for r in official}
    candidates = [r for r in targets if (r.get('league'), r.get('kickoff','')[:10], r.get('home'), r.get('away')) in keys]
    return {'scope': '275 official results vs 23 prior result-history rows and accuracy-review targets only; not every repository or production ledger',
      'official_results': len(official), 'unique_official_ids': len({r['official_game_id'] for r in official}),
      'prior_history': len(prior), 'prior_identity_links': len(linked['links']),
      'score_agreements': sum(r['reconciliation']['status'] == 'agree' for r in linked['links']),
      'prior_unmatched': len(linked['unmatched']), 'additional_result_only': len(official)-len(linked['links']),
      'review_targets_matching_identity': len(candidates), 'review_2025_k2_targets': sum(r.get('league') == 'K\ub9ac\uadf82' and r.get('kickoff','')[:4] == '2025' for r in targets),
      'same_product_price_and_prediction_and_result_in_review_targets': len(candidates),
      'certified_pregame_lineup_receipts_in_official_results': sum(bool(r.get('lineup')) and r.get('external_receipt_verified') is True for r in official),
      'source_sha256': {str(p.relative_to(ROOT)).replace('\\','/'): sha(p) for p in [CON/'official-2025.json', CON/'prior-2025.json', CON/'match.py', TARGET]}}

if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=True, indent=2))
