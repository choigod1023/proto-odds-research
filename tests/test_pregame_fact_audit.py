from src.pregame_fact_audit import audit, gate


def valid():
    return dict(id='a', sport='bs', kind='starter', event_id='official1',
                observed_at='2026-10-24T10:00:00+09:00',
                prediction_at='2026-10-24T11:00:00+09:00',
                kickoff_at='2026-10-24T12:00:00+09:00', source_url='https://analysis.example/1',
                verification=dict(confirmed=True, source_url='https://official.example/1',
                                  observed_at='2026-10-24T10:15:00+09:00'))


def test_fact_is_not_a_fitted_prediction():
    row = valid()
    assert gate(row) == 'eligible_for_feature_encoding'
    result = audit([row])
    assert result['sports']['bs']['roi_delta'] is None
    assert result['monthly_loss_limit_krw'] == 50000


def test_postgame_fetch_cannot_be_backdated_by_publication():
    row = valid()
    row.update(published_at='2026-10-24T09:00:00+09:00', observed_at='2026-10-25T10:00:00+09:00')
    assert gate(row) == 'not_available_at_frozen_prediction'


def test_naive_time_and_t30_and_unverified_are_rejected():
    row = valid()
    row['prediction_at'] = '2026-10-24T11:31:00+09:00'
    assert gate(row) == 'not_available_at_frozen_prediction'
    row = valid()
    row['observed_at'] = '2026-10-24T10:00:00'
    assert gate(row) == 'missing_aware_cutoff'
    row = valid()
    row['verification']['observed_at'] = '2026-10-25T10:00:00+09:00'
    assert gate(row) == 'missing_independent_pregame_confirmation'


def test_empty_samples_are_not_zero_roi():
    assert all(r['roi_delta'] is None for r in audit([])['sports'].values())
