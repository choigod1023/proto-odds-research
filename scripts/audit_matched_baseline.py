"""Inspect actual historical probability provenance, not today's model code."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


def audit(source):
    rows = json.loads(source.read_text(encoding='utf8'))['opportunity_ledger']
    cache = {}
    evidence = []
    basis = Counter()
    for row in rows:
        sha = row['snapshot_id']
        if sha not in cache:
            raw = subprocess.check_output(['git', 'show', sha + ':docs/data/picks_v2.json'])
            cache[sha] = (json.loads(raw), hashlib.sha256(raw).hexdigest())
        snapshot, digest = cache[sha]
        games = [g for g in snapshot['live'] if g.get('event_id') == row['id']]
        if len(games) != 1:
            raise ValueError('Ambiguous historical model event')
        opts = [o for o in games[0]['options'] if o.get('market') == '승패' and not o.get('label')]
        sides = {o['선택']: o for o in opts}
        if len(opts) != 2 or set(sides) != {'홈', '원정'}:
            raise ValueError('Missing unique historical moneyline sides')
        ordered = [sides[s] for s in ('홈', '원정')]
        final = [float(o['최종확률']) for o in ordered]
        normalized = [p / sum(final) for p in final]
        if any(abs(a-b) > 1e-10 for a, b in zip(normalized, row['model'])):
            raise ValueError('Comparison baseline differs from historical final probabilities')
        basis.update(str(o.get('확률근거')) for o in ordered)
        evidence.append(dict(id=row['id'],snapshot_id=sha,snapshot_sha256=digest,
            final_equals_market=all(o['최종확률'] == o.get('시장확률') for o in ordered),
            options=[{k:o.get(k) for k in ('선택','최종확률','시장확률','모델확률','확률근거','내부모델상태','내부모델사유')} for o in ordered]))
    return dict(input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        events=len(evidence),final_equals_market=sum(r['final_equals_market'] for r in evidence),
        probability_basis_counts=dict(basis),evidence=evidence,
        interpretation='Stored operating probabilities, not proof of a separately trained model. All comparison model-weight labels refer to these historical probabilities.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.input)
    with args.output.open('x', encoding='utf8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k:result[k] for k in ('events','final_equals_market','probability_basis_counts')}))
