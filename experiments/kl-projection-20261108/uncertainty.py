"""Paired calendar-week bootstrap; KL minus each fixed baseline."""
import json
from datetime import datetime
from pathlib import Path
import numpy as np

p = Path(__file__).parent
rows = json.loads((p / 'predictions.json').read_text(encoding='utf8'))
rng = np.random.default_rng(20261108)
out = {}
baselines = ('poisson_1x2', 'poisson_all', 'dc_all', 'market_devig')
for fam in ('승무패', '핸디캡', '언더오버'):
    def select(model):
        return [x for x in rows if x['model'] == model and x['family'] == fam and x['date'][:4] >= '2025']
    candidate = select('kl_soft')
    references = [select(name) for name in baselines]
    def identity(x):
        return (x['date'], x['league'], x['home'], x['away'], x['family'], x['line'], x['outcome'], x['odds'])
    assert all([identity(x) for x in ref] == [identity(x) for x in candidate] for ref in references)
    weeks = {}
    for i, y in enumerate(candidate):
        week = datetime.fromisoformat(y['date']).date().isocalendar()[:2]
        outcome = y['outcome']
        differences = []
        for ref in references:
            x = ref[i]
            differences.extend([(y['pick'] == outcome) - (x['pick'] == outcome),
                                y['profit'] - x['profit'],
                                -np.log(y['probabilities'][outcome]) + np.log(x['probabilities'][outcome])])
        weeks.setdefault(week, []).append(differences)
    blocks = [np.array(v) for v in weeks.values()]
    estimates = []
    for _ in range(3000):
        sample = rng.integers(len(blocks), size=len(blocks))
        estimates.append(np.concatenate([blocks[i] for i in sample]).mean(axis=0))
    values = np.concatenate(blocks).mean(axis=0)
    intervals = np.quantile(estimates, [.025, .975], axis=0)
    result = {'method': 'calendar week cluster bootstrap 3000 seed 20261108; same week draws for all baselines',
              'n': len(candidate), 'weeks': len(blocks)}
    for j, baseline in enumerate(baselines):
        result['kl_minus_' + baseline] = {
            name: {'estimate': float(values[3*j+i]), 'ci95': [float(intervals[0,3*j+i]), float(intervals[1,3*j+i])]}
            for i, name in enumerate(('hit', 'roi', 'logloss'))}
    out[fam] = result
(p / 'uncertainty.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf8')
