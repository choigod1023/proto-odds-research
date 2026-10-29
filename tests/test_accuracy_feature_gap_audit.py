import json

import pytest

from scripts.audit_accuracy_feature_gaps import selection_probe, temperature_probe, disagreement_audit


def test_recognized_context_does_not_change_the_current_vote_policy():
    probe = selection_probe()
    assert probe['synthetic'] and probe['distinct_selections'] == 1
    assert {r['selected'] for r in probe['records']} == {'원정'}
    assert {r['probability'] for r in probe['records']} == {.48}
    assert all(not e['available'] for e in probe['records'][0]['evidence'])
    assert all(e['available'] for r in probe['records'][1:] for e in r['evidence'])


def test_positive_temperature_changes_confidence_without_changing_choices():
    probe = temperature_probe(rows=100)
    assert len(probe['trials']) == 6
    assert all(t['changed_selections'] == 0 for t in probe['trials'])


def test_disagreement_audit_counts_unique_games_and_keeps_refunds_separate(tmp_path):
    def event(winner, market, vote, order=None):
        return dict(winner=winner, order=order or ['home','away'], voters=2,
            picks=dict(market=market, tipster_vote=vote, market90_vote10=market))
    path = tmp_path / 'cohort.json'
    path.write_text(json.dumps(dict(requested_window=['2025-10-29','2026-10-28'],
        event_ledger=[event('home','home','home'), event('away','home','away'),
                      event('draw','home','away'), event('home','home',None)])), encoding='utf8')
    result = disagreement_audit(path)
    assert result['common_games'] == 3
    assert result['groups']['agreement']['wins']['market'] == 1
    assert result['groups']['disagreement'] == dict(games=2, pushes=1,
        wins=dict(market=0, tipster_vote=1, market90_vote10=0))
    assert not result['new_predictive_model_tested']
