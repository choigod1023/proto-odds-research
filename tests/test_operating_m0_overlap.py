import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('audit', Path(__file__).resolve().parents[1] / 'scripts/audit_operating_m0_overlap.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_cutoff_requires_all_actual_timestamps_and_timezone():
    kickoff = '2026-11-03T18:30:00+09:00'
    assert audit.before_cutoff(kickoff, '2026-11-03T09:00:00Z')
    assert not audit.before_cutoff(kickoff, '2026-11-03T09:00:00.000001Z')
    assert not audit.before_cutoff(kickoff, '2026-11-03T09:00:00Z', None)
    assert not audit.before_cutoff(kickoff, '2026-11-03T09:00:00')
    assert not audit.before_cutoff(kickoff)


def test_audit_never_promotes_a_fixture_match_to_a_forecast(tmp_path):
    root, state, vote = (tmp_path / x for x in ('root', 'state', 'vote'))
    def put(path, obj, jsonl=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(('\n'.join(json.dumps(x) for x in obj) + '\n') if jsonl else json.dumps(obj), encoding='utf8')
    put(root / 'data/raw/prediction_ledger/pregame.jsonl', [], True)
    put(root / 'data/raw/recommendation_revisions.jsonl', [], True)
    put(state / 'last-run.json', {})
    put(state / 'history-preflight.json', {})
    put(vote / 'input-manifest.json', {})
    t = '2026-11-03T09:30:00Z'
    put(vote / 'inputs.json', [{'sport': 'bs', 'fixture': {'league': 'KBO', 'home': 'KT', 'away': 'SS'}, 'kickoff': t, 'snapshot_id': 'test', 'as_of': '2026-11-03T08:00:00Z', 'captured_at': '2026-11-03T08:00:00Z', 'market_observed_at': '2026-11-03T08:00:00Z', 'evidence': []}])
    put(state / 'paired-ledger.jsonl', [{'type': 'opportunity', 'event_key': 'key', 'observed_at': '2026-11-03T09:39:00Z', 'event': {'home_name': 'KT', 'away_name': 'SS', 'kickoff_at': t}}], True)
    result = audit.audit(root, state, vote)
    assert result['strict_common_sample']['count'] == 0
    assert result['candidate_fixture_matches'][0]['vote_times_before_cutoff'] is False
    assert result['candidate_fixture_matches'][0]['opportunity_before_cutoff'] is False
    assert result['candidate_fixture_matches'][0]['forward_forecast_records_for_event'] == 0
