import datetime as dt
import hashlib
import json
from pathlib import Path

def test_stored_nba_audit():
    path = Path('experiments/operating-ledger-comparison-20261029/comparison.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    rows = [r for r in data['events'] if r['fixture']['league'] == 'NBA']
    parse = lambda s: dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    counts = {
        'artifact_nba_rows': len(rows),
        'unique_event_ids': len({r['event_id'] for r in rows}),
        'captured_at_by_t30': sum(parse(r['captured_at']) <= parse(r['kickoff']) - dt.timedelta(minutes=30) for r in rows),
        'market_reference_selected_with_saved_odds': sum(r['arms']['market_reference']['status'] == 'selected' and r['arms']['market_reference'].get('odds') is not None for r in rows),
        'result_linked': sum(r['label_status'] == 'settled' for r in rows),
        'operating_selected_with_odds_and_result': sum(r['label_status'] == 'settled' and r['arms']['operating']['status'] == 'selected' and r['arms']['operating'].get('odds') is not None for r in rows),
        'operating_withheld': sum(r['arms']['operating']['status'] == 'abstained' for r in rows),
        'official_player_documents_in_event_rows': sum(any(k in r for k in ('player_documents', 'official_injury_report', 'confirmed_lineup')) for r in rows),
    }
    assert counts == {'artifact_nba_rows':35, 'unique_event_ids':35, 'captured_at_by_t30':35, 'market_reference_selected_with_saved_odds':35, 'result_linked':32, 'operating_selected_with_odds_and_result':20, 'operating_withheld':15, 'official_player_documents_in_event_rows':0}, counts
    assert all(r['capture_kind'] == 'retrospective_saved_input_replay' for r in rows)
    print(json.dumps({'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'counts':counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_stored_nba_audit()
