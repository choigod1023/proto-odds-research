"""Connect identity without outcome data, then reconcile the score."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def key_prior(row):
    return (row['league'], row['date'], row['home'], row['away'])

def key_official(row):
    return (row['league'], row['kickoff_at'][:10], row['home'], row['away'])

def link(prior, official):
    official_ids = [row['official_game_id'] for row in official]
    if any(not isinstance(ident, str) or not ident.strip() for ident in official_ids):
        raise ValueError('missing official game ID')
    if len(official_ids) != len(set(official_ids)):
        raise ValueError('duplicate official game ID')
    index = defaultdict(list)
    for row in official:
        index[key_official(row)].append(row)
    prior_counts = Counter(key_prior(row) for row in prior)
    links, unmatched = [], []
    for row in prior:
        key = key_prior(row)
        candidates = index[key]
        if len(candidates) != 1 or prior_counts[key] != 1:
            unmatched.append({'prior_key': list(key), 'candidate_count': len(candidates),
                              'reason': 'ambiguous' if len(candidates) > 1 or prior_counts[key] > 1 else 'no_candidate'})
            continue
        official_row = candidates[0]
        old_score = [row['hg'], row['ag']]
        new_score = official_row['score']
        links.append({'prior_key': list(key), 'official_game_id': official_row['official_game_id'],
                      'reconciliation': {'prior_score': old_score, 'official_score': new_score,
                                         'status': 'agree' if old_score == new_score else 'disagree'}})
    return {'links': links, 'unmatched': unmatched}

def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()

def main():
    prior_path, official_path = ROOT / 'prior-2025.json', ROOT / 'official-2025.json'
    prior = json.loads(prior_path.read_text(encoding='utf8'))
    official = json.loads(official_path.read_text(encoding='utf8'))
    result = link(prior, official)
    result['input_sha256'] = {'prior_derived_lf_bytes': sha(prior_path), 'official_derived_lf_bytes': sha(official_path)}
    (ROOT / 'links.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps({'prior':len(prior), 'official':len(official), 'links':len(result['links']),
                      'unmatched':len(result['unmatched']), 'agree':sum(x['reconciliation']['status']=='agree' for x in result['links']),
                      'additional':len(official)-len(result['links'])}))

if __name__ == '__main__':
    main()
