"""Independent settlement and matched-arm integrity check; no model imports."""
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify():
    report = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    arms = {}
    probability_replays = {}
    replay_path = HERE.parent / 'all-sports-underdog-20261031/replay_audit.py'
    spec = importlib.util.spec_from_file_location('independent_chronology_replay', replay_path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    forecasts = 0
    for arm, summary in report['by_arm'].items():
        with gzip.open(HERE / arm / 'ledger.jsonl.gz', 'rt', encoding='utf-8') as stream:
            rows = [json.loads(line) for line in stream]
        assert len(rows) == report['fixtures'] == len({r['id'] for r in rows})
        arms[arm] = {r['id']: r for r in rows}
        states = json.loads((HERE / arm / 'states.json').read_text(encoding='utf-8'))
        probability_replays[arm] = replay.check(rows, rows, states, refit=True)
        for model, policies in summary.items():
            for row in rows:
                forecast = row['predictions'][model]
                p = forecast['p']
                assert len(p) == 3 and all(0 <= x <= 1 for x in p) and abs(sum(p)-1) < 1e-12
                pick = max(range(3), key=lambda i: p[i])
                hit = pick == row['y']
                net = Decimal(str(row['odds'][pick]))-1 if hit else Decimal(-1)
                assert (forecast['pick'], forecast['hit'], Decimal(forecast['net'])) == (pick, hit, net)
                forecasts += 1
            for policy, cell in policies.items():
                selected = [r for r in rows if policy == 'all' or
                            r['market_p'][r['predictions'][model]['pick']] < max(r['market_p'])-1e-12]
                total = sum((Decimal(r['predictions'][model]['net']) for r in selected), Decimal(0))
                hits = sum(r['predictions'][model]['hit'] for r in selected)
                assert cell['bets'] == len(selected) and cell['hits'] == hits
                assert Decimal(cell['net_units']) == total
                assert cell['roi'] == (float(total/len(selected)) if selected else None)
                assert cell['accuracy'] == (hits/len(selected) if selected else None)
    old, new = (arms[k] for k in ('source_dates', 'two_witnessed_corrections'))
    assert old.keys() == new.keys()
    moved = []
    for identity in old:
        a, b = old[identity], new[identity]
        for key in ('id','sport','league','cohort','home','away','odds','y'):
            assert a[key] == b[key]
        if a['day'] != b['day']:
            moved.append(identity)
    assert len(moved) == report['corrected_dates'] == 2
    assert report['adopted'] is False and not report['player_or_tipster_inputs_used']
    return dict(forecasts_checked=forecasts, fixture_date_changes=len(moved),
                same_event_price_and_label=True, strict_performance_certified=False,
                independent_probability_replay=probability_replays,
                files={str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(HERE.rglob('*')) if p.is_file() and
                       (p.suffix == '.gz' or p.name in ('protocol.json','witnesses.json','results.json'))})


if __name__ == '__main__':
    result = verify()
    (HERE / 'verification.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}))
