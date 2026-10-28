"""Independently settle saved yearly picks and reconcile the archived legacy arms."""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_DOWN
import argparse
import hashlib
import json
from pathlib import Path


def verify(folder, upstream=None):
    findings = {}
    manifests = json.loads((folder / 'inputs.json').read_text(encoding='utf8'))
    for manifest, filename in zip(manifests, ('strict-results.json', 'standard-ot-results.json')):
        raw = (folder / manifest['output']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest['output_sha256']
        rows = [json.loads(line) for line in raw.decode('utf8').splitlines()]
        data = json.loads((folder / filename).read_text(encoding='utf8'))
        assert data['input_sha256'] == manifest['output_sha256']
        assert len(rows) == manifest['records']
        assert data['rows_claiming_independent_pregame_evidence'] == 0
        originals = defaultdict(list)
        for row in rows:
            originals[(row['league'], row['fixture_id'])].append(row)
        settlements = {}
        events = data['event_ledger']
        for event in events:
            key = event['league'], event['fixture_id']
            assert key not in settlements
            batch = originals[key]
            scores = {(r['home_score'], r['away_score']) for r in batch}
            assert len(scores) == 1
            home, away = scores.pop()
            winner = 'home' if home > away else 'away' if away > home else 'draw'
            assert event['winner'] == winner
            assert any(r.get('proto_offer_evidence') == 'archived_exact_fixture'
                       and r['proto_odds'] == event['prices'] for r in batch)
            outcomes = {}
            for model, pick in event['picks'].items():
                if pick is None:
                    outcomes[model] = None
                elif winner == 'draw' and len(event['order']) == 2:
                    outcomes[model] = ('push', Decimal(0))
                elif pick == winner:
                    outcomes[model] = ('win', Decimal(str(event['prices'][event['order'].index(pick)])) - 1)
                else:
                    outcomes[model] = ('loss', Decimal(-1))
            settlements[key] = outcomes
        common = [e for e in events if all(p is not None for p in e['picks'].values())]
        for scope, sample in [('model_specific', events), ('common', common)]:
            for model, stats in data['comparison'][scope].items():
                scored = [settlements[(e['league'], e['fixture_id'])][model] for e in sample]
                chosen = [s for s in scored if s is not None]
                counts = Counter(s[0] for s in chosen)
                assert [counts['win'], counts['loss'], counts['push']] == [
                    stats['wins'], stats['losses'], stats['pushes']]
                assert len(sample) - len(chosen) == stats['abstained']
                if chosen:
                    assert abs(float(sum(s[1] for s in chosen) / len(chosen)) - stats['unit_roi']) < 1e-12
        for model, account in data['capped_paper'].items():
            daily = defaultdict(list)
            totals = defaultdict(int)
            seen = set()
            for entry in account['ledger']:
                key = entry['league'], entry['fixture_id']
                assert key not in seen
                seen.add(key)
                state, unit = settlements[key][model]
                expected = int((unit * 10000).to_integral_value(rounding=ROUND_DOWN))
                assert entry['net_krw'] == expected
                daily[entry['date']].append(entry)
            for day, entries in sorted(daily.items()):
                month = day[:7]
                assert totals[month] - 10000 * len(entries) >= -50000
                totals[month] += sum(e['net_krw'] for e in entries)
                assert totals[month] >= -50000
            assert dict(totals) == account['monthly_net']
            assert sum(totals.values()) == account['profit_krw']
            assert len(seen) == account['bets']
        legacy_checked = False
        if upstream:
            source_raw = (upstream / manifest['source_file']).read_bytes().replace(b'\r\n', b'\n')
            assert hashlib.sha256(source_raw).hexdigest() == manifest['source_sha256_lf']
            old = json.loads(source_raw)
            old_events = {(e['league'], e['fixture_id']): e for e in old['paired_ledger']
                          if data['requested_window'][0] <= e['match_date'] <= data['requested_window'][1]}
            assert set(old_events) == {(e['league'], e['fixture_id']) for e in common}
            aliases = dict(market='archived_market_reference', tipster_vote='tipster',
                           market90_vote10='market90_new_tipster10')
            for event in common:
                prior = old_events[(event['league'], event['fixture_id'])]
                for model, legacy in aliases.items():
                    assert event['picks'][model] == prior['picks'][legacy]
                    assert abs(float(settlements[(event['league'], event['fixture_id'])][model][1])
                               - prior['unit_net'][legacy]) < 1e-12
            legacy_checked = True
        findings[filename] = dict(input_records=len(rows), priced_fixtures=len(events),
            common_fixtures=len(common), independently_resettled_models=5,
            monthly_loss_cap_reconciled=True, legacy_three_arm_fixture_parity=legacy_checked)
    return findings


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--upstream', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    findings = verify(args.folder, args.upstream)
    rendered = json.dumps(findings, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf8')
    print(rendered)
