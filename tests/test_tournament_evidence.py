import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tournament_evidence import tournament_features, tipster_features

PRED = '2026-10-24T09:00:00Z'
KICK = '2026-10-24T10:00:00Z'


def series(**kw):
    return dict(event_id='e', observed_at='2026-10-24T08:00:00Z',
                competition_id='example', season='2026', rules_version='v1',
                source_url='https://example.org/rules', stage='final',
                format='win_target_series', home_wins=3, away_wins=2,
                home_wins_required=4, away_wins_required=4) | kw


def tip(**kw):
    return dict(event_id='e', observed_at='2026-10-24T08:00:00Z',
                published_at='2026-10-24T07:00:00Z', canonical_author_id='a',
                source_url='https://example.org/pick', pick_id='1', market='winner',
                settlement_scope='regulation', line=None, selection='home') | kw


def crowd(rows):
    return tipster_features(rows, 'e', PRED, KICK, market='winner',
                            settlement_scope='regulation', line=None,
                            selections=('home', 'away', 'draw'))


def test_series_and_asymmetric_targets():
    f = tournament_features(series(), 'e', PRED, KICK)['features']
    assert f['home_can_clinch'] and f['away_elimination_risk']
    assert not f['home_elimination_risk']
    f = tournament_features(series(home_wins=0, away_wins=0,
                                  home_wins_required=1, away_wins_required=2),
                            'e', PRED, KICK)['features']
    assert f['home_can_clinch'] and not f['away_can_clinch']


@pytest.mark.parametrize('kw', [dict(home_wins=4), dict(away_wins=-1),
                               dict(home_wins=True), dict(home_wins_required=0)])
def test_bad_series(kw):
    assert tournament_features(series(**kw), 'e', PRED, KICK)['status'] != 'eligible'


def test_missing_and_future_context_not_assumed_regular():
    for kw in [dict(rules_version=None), dict(observed_at='2026-10-24T11:00:00Z'),
               dict(format='aggregate_two_leg')]:
        assert tournament_features(series(**kw), 'e', PRED, KICK)['status'] != 'eligible'


def test_duplicates_not_independent_and_conflicts_excluded():
    r = crowd([tip(), tip(source_url='https://other.org', pick_id='2')])
    assert r['author_count'] == 1
    assert crowd([tip(), tip(selection='away')])['author_count'] == 0


def test_latest_predecision_opinion_and_future_excluded():
    r = crowd([tip(), tip(published_at='2026-10-24T07:30:00Z', selection='away'),
               tip(observed_at='2026-10-24T09:01:00Z', selection='home')])
    assert r['selection_counts']['away'] == 1 and r['author_count'] == 1


@pytest.mark.parametrize('kw', [dict(settlement_scope='qualification'),
                               dict(market='handicap'), dict(line=1.5),
                               dict(canonical_author_id=None), dict(published_at=None),
                               dict(published_at='2026-10-24T08:01:00Z')])
def test_wrong_market_identity_or_time(kw):
    assert crowd([tip(**kw)])['author_count'] == 0


def test_settlement_and_leaderboard_do_not_change_features():
    assert crowd([tip()]) == crowd([tip(result='win', leaderboard_roi=99)])
