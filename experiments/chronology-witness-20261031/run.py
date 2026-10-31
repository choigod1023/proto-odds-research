"""Registered offline diagnostic; NEVER a strict performance certification."""
from collections import Counter
import argparse
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'src'))
from research_chronology import DateWitnesses, audit_archive

INPUT_SHA = '31c4e7bc3a4dfc4bafa1bcb70ba295e99d910b3e326c14bfffe5bb0bf1613605'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--normalized', type=Path, required=True)
    parser.add_argument('--model-code', type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads((HERE / 'protocol.json').read_text(encoding='utf-8'))
    model_hash = hashlib.sha256(args.model_code.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if model_hash != protocol['model_code_lf_sha256']:
        raise ValueError('model code differs from preregistered upstream')
    if hashlib.sha256(args.normalized.read_bytes()).hexdigest() != INPUT_SHA:
        raise ValueError('normalized input differs from preregistered upstream')
    registry = json.loads((HERE / 'witnesses.json').read_text(encoding='utf-8'))
    witness = DateWitnesses(registry, args.raw.read_bytes())
    spec = importlib.util.spec_from_file_location('pinned_common', args.model_code)
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    common.save(HERE / 'archive-audit.json', audit_archive(args.raw, registry))
    rows = [r for r in common.read(args.normalized) if r['league'] == protocol['sensitivity_league']]
    from urllib.parse import quote
    changes = {}
    for key, item in witness.witnesses.items():
        identity = 'extfoot|' + '|'.join(quote(x, safe='') for x in key[:4])
        if identity not in {r['id'] for r in rows}:
            raise ValueError('date witness not found in normalized sensitivity cohort')
        row = next(r for r in rows if r['id'] == identity)
        expected = 0 if key[4] > key[5] else 1 if key[4] == key[5] else 2
        if row['y'] != expected:
            raise ValueError('normalized label differs from witness score')
        changes[identity] = item['confirmed_day']
    corrected = [{**r, 'day': changes[r['id']], 'kickoff': changes[r['id']] + 'T00:00:00'}
                 if r['id'] in changes else dict(r) for r in rows]
    if len({(r['league'], r['day'], r['home'], r['away']) for r in corrected}) != len(corrected):
        raise ValueError('correction creates duplicate fixture identity')
    ledgers, summaries = {}, {}
    for arm, records in [('source_dates', rows), ('two_witnessed_corrections', corrected)]:
        folder = HERE / arm
        folder.mkdir(exist_ok=True)
        ledger, states = common.forecasts(records)
        common.dump(folder / 'ledger.jsonl.gz', ledger)
        common.save(folder / 'states.json', states)
        ledgers[arm] = {r['id']: r for r in ledger}
        summaries[arm] = {m: {p: common.metrics(ledger, m, p) for p in protocol['policies']}
                          for m in protocol['models']}
    old, new = ledgers.values()
    assert old.keys() == new.keys()
    for identity in old:
        assert (old[identity]['odds'], old[identity]['y']) == (new[identity]['odds'], new[identity]['y'])
    metrics = {}
    for model in protocol['models']:
        changed = [i for i in old if old[i]['predictions'][model]['pick'] != new[i]['predictions'][model]['pick']]
        delta = sum((Decimal(new[i]['predictions'][model]['net']) - Decimal(old[i]['predictions'][model]['net'])
                     for i in old), Decimal(0))
        probability_delta = max(abs(a-b) for i in old for a,b in zip(
            old[i]['predictions'][model]['p'], new[i]['predictions'][model]['p']))
        metrics[model] = dict(changed_picks=len(changed), net_delta_units=str(delta),
                              max_probability_delta=probability_delta,
                              roi_delta_percentage_points=float(delta/len(old)*100))
    result = dict(design=protocol['sensitivity_design'], fixtures=len(rows), corrected_dates=len(changes),
                  known_unverified_dates_remaining=len(rows)-len(changes),
                  strict_comparison_status='blocked_unverified_chronology_and_pregame_receipts',
                  source_range=[min(r['day'] for r in rows), max(r['day'] for r in rows)],
                  fallback_counts={arm: sum(r['fallback'] for r in ledger.values()) for arm, ledger in ledgers.items()},
                  by_arm=summaries, differences=metrics,
                  raw_sha256=witness.source_sha256, normalized_sha256=INPUT_SHA, model_code_lf_sha256=model_hash,
                  protocol_sha256=hashlib.sha256((HERE / 'protocol.json').read_bytes()).hexdigest(),
                  witness_sha256=hashlib.sha256((HERE / 'witnesses.json').read_bytes()).hexdigest(),
                  player_or_tipster_inputs_used=False, adopted=False,
                  note='Sensitivity ONLY. Other dates are unverified. NOR-only fits differ from the prior 38-league pooled experiment. No new ROI edge claim.')
    common.save(HERE / 'results.json', result)
    print(json.dumps({k: result[k] for k in ('fixtures','corrected_dates','differences','strict_comparison_status')}))


if __name__ == '__main__':
    main()
