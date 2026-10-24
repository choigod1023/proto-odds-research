import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tipster_facts_only import assess, DUPLICATES

PRED = '2025-05-05T10:00:00+09:00'
START = '2025-05-05T14:00:00+09:00'


def fixture():
    return dict(event_id='event-1', kind='injury', side='home', value=1,
                source_url='https://analysis.example/article',
                observed_at='2025-05-05T09:00:00+09:00',
                verification=dict(event_id='event-1', confirmed=True,
                    source_url='https://official.example/record',
                    observed_at='2025-05-05T09:30:00+09:00'))


def check(c):
    return assess(c, 'event-1', PRED, START)


def test_pick_fields_cannot_affect_output():
    c = fixture()
    original = check(c)
    for pick in ('home', 'away', 'over', 'under', 'handicap'):
        c.update(final_pick=pick, text='승리 추천 ' + pick, odds=999, result='won')
        assert check(c) == original
    assert original['status'] == 'eligible'


def test_reject_recommendation_type():
    c = fixture()
    for kind in ('final_pick', 'handicap', 'total', 'prediction', 'opinion'):
        c['kind'] = kind
        assert check(c)['status'] == 'not_a_fact_type'


def test_late_and_missing_observation_rejected():
    c = fixture()
    c['observed_at'] = None
    assert check(c)['status'] == 'missing_or_naive_observation_time'
    c['observed_at'] = '2026-10-24T12:00:00+09:00'
    assert check(c)['status'] == 'observed_after_prediction'


def test_verification_must_be_independent_and_pregame():
    c = fixture()
    c['verification']['confirmed'] = False
    assert check(c)['status'] == 'not_independently_verified'
    c = fixture()
    c['verification']['observed_at'] = START
    assert check(c)['status'] == 'verification_observed_after_prediction'
    c = fixture()
    c['verification']['event_id'] = 'other'
    assert check(c)['status'] == 'verification_event_mismatch'


def test_event_and_cutoff():
    c = fixture()
    assert assess(c, 'other', PRED, START)['status'] == 'event_mismatch'
    assert assess(c, 'event-1', START, START)['status'] == 'prediction_not_pregame'


def test_no_text_or_nan_features():
    for value in ('LG win', float('nan'), float('inf'), []):
        c = fixture()
        c['value'] = value
        assert check(c)['status'] in ('unencoded_fact', 'invalid_value')
    c = fixture()
    c['side'] = 'winner'
    assert check(c)['status'] == 'invalid_side'


def test_form_rest_overlap_not_new_signal():
    c = fixture()
    for kind in ('recent_form', 'rest'):
        c['kind'] = kind
        assert check(c)['existing_feature_overlap'] == DUPLICATES[kind]
    c['kind'] = 'pitcher_form'
    assert check(c)['existing_feature_overlap'] == []


def test_same_article_is_not_independent_confirmation():
    c = fixture()
    c['verification']['source_url'] = c['source_url']
    assert check(c)['status'] == 'not_independently_verified'


def test_real_manifest_has_zero_eligible_claims_and_no_pick_fields():
    import json
    rows = json.loads((Path(__file__).resolve().parents[1] /
        'experiments/tipster-facts-only-20261024/claims.json').read_text(encoding='utf8'))
    assert len(rows) == 11
    for row in rows:
        assert not {'final_pick', 'choice', 'handicap', 'total'} & row.keys()
        assert assess(row, row['event_id'], None, None)['status'] != 'eligible'
