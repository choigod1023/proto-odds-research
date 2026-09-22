from datetime import datetime
import json
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from match_api import KST, MatchViews, card_game, game_key, summary
from runtime_db import RuntimeDatabase


def game(date='09.22(화) 18:00', year=2026):
    return dict(year=year, round=112, sport='bs', league='KBO', date=date, home='홈', away='원정',
                options=[{'selection_id':'s', '적중':True, '배당':1.8}],
                prediction_record={'selection_id':'s','result':'hit'},
                선발={'home':'투수', 'home_detail':{'name':'투수','history':['x']*1000}})


def test_summary_preserves_decision_and_separates_detail_history():
    current=game(); old=game('08.01(토) 18:00'); overnight=game('09.21(월) 23:00')
    payload={'live':[current,overnight], 'past':[old], 'prediction_performance':{'records':{'old':{}}}}
    before=json.dumps(payload)
    result=summary(payload,'r',now=datetime(2026,9,22,tzinfo=KST))
    assert len(result['live'])==2 and not result['past']
    assert 'prediction_performance' not in result
    assert len(result['result_games'])==3
    assert result['live'][0]['options']==current['options']
    assert result['live'][0]['prediction_record']==current['prediction_record']
    assert 'history' not in result['live'][0]['선발']['home_detail']
    assert json.dumps(payload)==before
    assert len(summary(payload,'r','all')['past'])==1


def test_year_boundary_and_collision_safe_identity():
    rows=[game('12.31(목) 23:00'),game('01.01(금) 10:00',2027),game('01.01(목) 10:00',2026)]
    result=summary({'live':rows},'r',now=datetime(2027,1,1,tzinfo=KST))
    assert len(result['live'])==2
    assert game_key(rows[1])!=game_key(rows[2])


def test_detail_revision_and_cache(tmp_path, monkeypatch):
    db=RuntimeDatabase(tmp_path/'test.sqlite3')
    current=game(); db.store_artifact('picks_v2',{'live':[current]})
    views=MatchViews(db)
    result=views.get('all')
    monkeypatch.setattr(db,'get_artifact_json',lambda *args: pytest.fail('unchanged payload reread'))
    assert views.get('all') is result
    detail=views.get('detail',game_key(current),result['view']['revision'])
    assert detail['game']==current
    with pytest.raises(ValueError): views.get('detail',game_key(current),'stale')
    with pytest.raises(KeyError): views.get('detail','bad',result['view']['revision'])
    with pytest.raises(ValueError): views.get('bad')


def test_compressed_cache_preserves_views_and_invalidates_revision(tmp_path):
    db = RuntimeDatabase(tmp_path/'cache.sqlite3')
    original = game()
    payload = {'live': [original], 'past': [game('08.01(토) 18:00')],
               'prediction_performance': {'records': {'large': ['x'] * 1000}}}
    db.store_artifact('picks_v2', payload)
    views = MatchViews(db)
    first = views.get('all')
    revision = first['view']['revision']
    assert first == summary(payload, revision, 'all')
    assert 'prediction_performance' not in views.payload
    assert 'history' not in views.payload['live'][0]['선발']['home_detail']
    assert all(isinstance(value, bytes) for value in views.games.values())
    detail = views.get('detail', game_key(original), revision)
    assert detail['game'] == original
    detail['game']['options'].clear()
    assert views.get('detail', game_key(original), revision)['game'] == original
    updated = game(); updated['options'][0]['배당'] = 2.0
    db.store_artifact('picks_v2', {'live': [updated]})
    # Guarantee a different revision even on platforms with coarse clock resolution.
    with db.connect() as connection:
        connection.execute("UPDATE artifacts SET stored_at='next' WHERE name='picks_v2'")
    assert views.get('all') == summary({'live': [updated]}, 'next', 'all')
    with pytest.raises(ValueError): views.get('detail', game_key(original), revision)
    assert views.get('detail', game_key(updated), 'next')['game'] == updated
