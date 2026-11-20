import json

from scripts.run_tipster_year_audit import build


def inputs(tmp_path, candidate, final):
    c, f = tmp_path/'c.json', tmp_path/'f.json'
    c.write_text(json.dumps([candidate]), encoding='utf8')
    f.write_text(json.dumps([final]), encoding='utf8')
    return c,f


def test_listed_basketball_price_and_unknown_overtime_stay_separate(tmp_path):
    c,f = inputs(tmp_path,
        dict(source='Betblog',author='a',source_url='https://www.betblog.com/picks/x',
            league='NBA',match_date='2026-01-01',teams=['Bucks','Celtics'],side='home',
            selected_team='Bucks',market='moneyline',market_scope='unverified_overtime_rule',
            reported_decimal_odds=2.1),
        dict(league='NBA',match_date='2026-01-01',home='Milwaukee Bucks',away='Boston Celtics',
            home_score=120,away_score=110,official_id='nba:1',source_url='https://www.nba.com/games?date=2025-12-31'))
    strict = build([c],[f],[],tmp_path/'out')
    assumed = json.loads((tmp_path/'out/results-standard-ot.json').read_text(encoding='utf8'))
    assert strict['coverage']['picks']==0
    assert assumed['coverage']['picks']==1
    assert assumed['by_source']['Betblog']['quoted_roi']==1.1
    assert assumed['coverage']['certified_pregame_picks']==0


def test_international_local_date_joins_kst_game_and_proto_price(tmp_path):
    c,f = inputs(tmp_path,
        dict(source='Pickswise',author='a',url='https://www.pickswise.com/mlb/test',
            league='MLB',match_date='2025-10-28',teams=['Toronto Blue Jays','Los Angeles Dodgers'],
            selected_team='Dodgers',market='moneyline',period='full_game',
            reported_decimal_odds=2.,odds_time_quality='original_recommendation_claim'),
        dict(league='MLB',date='2025-10-28',start_at='2025-10-29T00:00:00Z',home='Los Angeles Dodgers',
            away='Toronto Blue Jays',home_score=2,away_score=6,official_id='mlb:1',
            source_url='https://statsapi.mlb.com/api/v1/schedule'))
    o=tmp_path/'o.json'
    o.write_text(json.dumps([dict(league='MLB',date='2025-10-29',home='LA다저스',away='토론블루',
        home_score=2,away_score=6,odds=[1.5,2.3])]),encoding='utf8')
    r=build([c],[f],[o],tmp_path/'out')
    assert r['coverage']['picks']==1
    assert r['accepted'][0]['match_date']=='2025-10-29'
    assert r['paired_author_consensus']['n']==1
    assert r['official_result_inputs_by_league']=={'MLB':1}
