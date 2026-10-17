"""Independent saved-record checks; does not rerun or tune the experiment."""
import hashlib
import json
from pathlib import Path
import numpy as np
import lotto_waiting_expanded as experiment


def main():
    root = experiment.old.ROOT
    folder = experiment.OUT
    read = lambda name: json.loads((folder / name).read_text(encoding='utf-8'))
    protocol, result, records = map(read, ('protocol.json', 'results.json', 'records.json'))
    assert result['status'] == 'completed' and len(records) == 256
    assert hashlib.sha256((root / 'scripts/lotto_waiting_expanded.py').read_bytes()).hexdigest() == protocol['source_sha256']
    for name, expected in protocol['inputs'].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    prior = read('prior-32.json')
    for i, row in enumerate(records):
        assert row['seed'] == 202610171000 + i
        assert len(row['hits']) == 21 and min(row['hits']) > 0
        assert max(row['hits']) <= row['generated']
        assert len(set(row['hits'])) == 21
        if i < 32:
            assert row['hits'][:11] == prior[i]['first_hits']
    waits = np.array([r['hits'] for r in records])
    order = sorted(range(256), key=lambda i: (waits[i, 0], i))
    chosen, others = order[:64], order[64:]
    assert result['selected_seeds'] == [records[i]['seed'] for i in chosen]
    null = np.load(folder / 'null.npz')['log_ratios']
    assert null.shape == (9999, 3) and np.isfinite(null).all()
    for j, (name, p_name, cols) in enumerate([
        ('old10', 'primary_old10', range(1, 11)),
        ('added10', 'secondary_added10', range(11, 21)),
        ('all20', 'descriptive_all20', range(1, 21)),
    ]):
        a = np.log(waits[np.ix_(chosen, list(cols))]).mean()
        b = np.log(waits[np.ix_(others, list(cols))]).mean()
        assert np.isclose(np.exp(a-b), result['geometric_ratios'][name])
        assert (1 + int((null[:, j] <= a-b).sum())) / 10000 == result['p_values'][p_name]
    assert sum(r['generated'] for r in records) == result['generated_ranks_actual']
    bezout = result['bezout']
    assert bezout['a']*bezout['bezout_x'] + bezout['modulus']*bezout['bezout_y'] == 1
    assert bezout['unique'] == bezout['actual_enumerated'] == experiment.old.SPACE
    assert bezout['target_first_hit'] == (bezout['bezout_x']*(protocol['target_ranks'][0]-bezout['b'])) % bezout['modulus'] + 1
    rows = [json.loads(line) for name in ('base.jsonl', 'extra.jsonl') for line in (folder/name).read_text().splitlines()]
    draws = {r['draw_no']: r['numbers_sorted'] for r in rows}
    for draw, rank in zip(protocol['rounds'], protocol['target_ranks']):
        assert experiment.old.unrank(rank) == draws[draw]
    paths = [folder/name for name in ('protocol.json', 'records.json', 'results.json', 'null.npz', 'base.jsonl', 'extra.jsonl', 'prior-32.json')]
    paths += [root/name for name in ('scripts/lotto_waiting_expanded.py', 'scripts/lotto_waiting_study.py', 'scripts/entropy_support/lotto_nonlinear.py')]
    audit = {'status': 'passed', 'records': len(records), 'first_hit_records': waits.size, 'prior32_exact': True,
             'sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    experiment.save('verification.json', audit)
    print(json.dumps({k:v for k,v in audit.items() if k != 'sha256'}))


if __name__ == '__main__':
    main()
