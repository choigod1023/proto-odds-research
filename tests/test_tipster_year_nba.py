import json

import pytest

from scripts.collect_tipster_year_nba import parse_page


def page(**changes):
    card = dict(gameId='0022500142',leagueId='00',gameStatus=3,
        gameTimeEastern='2025-11-01T17:00:00Z',gameTimeUtc='2025-11-01T21:00:00Z',
        homeTeam=dict(teamTricode='MIL',teamName='Bucks',score=133),
        awayTeam=dict(teamTricode='SAC',teamName='Kings',score=135))
    card.update(changes)
    obj = dict(props=dict(pageProps=dict(hasError=False,selectedDate='2025-11-01',
        gameCardFeed=dict(modules=[dict(cards=[dict(cardType='game',cardData=card)])]))))
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(obj)+'</script>'


def test_official_local_day_is_distinct_from_kst_and_only_final_scores_count():
    r = parse_page(page(), '2025-11-01', 'observed-now')[0]
    assert r['official_date']=='2025-11-01'
    assert r['match_date']=='2025-11-02'
    assert r['away']=='Sacramento Kings' and r['away_score']==135
    assert not parse_page(page(gameStatus=2),'2025-11-01','now')


def test_page_date_echo_and_final_score_validation_prevent_wrong_day_join():
    with pytest.raises(ValueError,match='requested date'):
        parse_page(page(),'2025-11-02','now')
    with pytest.raises(ValueError,match='date mismatch'):
        parse_page(page(gameTimeEastern='2025-11-02T17:00:00Z'),'2025-11-01','now')
    with pytest.raises(ValueError,match='invalid final'):
        parse_page(page(homeTeam=dict(teamTricode='MIL',teamName='Bucks',score=135)), '2025-11-01','now')


def test_official_exhibition_guest_does_not_discard_all_other_games():
    excluded=[]
    assert parse_page(page(awayTeam=dict(teamTricode='LON',teamName='London Lions',score=99)),
                      '2025-11-01','now',excluded)==[]
    assert excluded[0]['reason']=='non_NBA_opponent_in_official_exhibition'
