from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from baseball_candidate_watch import run_once,_fixtures
from runtime_db import RuntimeDatabase
from mlb_candidate_feed import extract
from test_mlb_candidate_feed import feed

NOW=datetime(2026,10,29,13,0,tzinfo=timezone.utc)


def fixtures(db):
    g=dict(league='MLB',game_id='1',game_datetime='2026-10-29T15:00:00Z',home_team='Home',away_team='Away')
    db.put_document('player_info',dict(games=[dict(g,padding='x'*1000000),dict(g,game_id='2',league='NBA')]))
    return g


def test_large_player_document_never_deserialized_and_source_budget_is_small(tmp_path,monkeypatch):
    import mlb_candidate_feed,kbo_candidate_source
    db=RuntimeDatabase(tmp_path/'db.sqlite3');g=fixtures(db)
    calls=[]
    source=extract(feed(),game_pk=1,scheduled_at=g['game_datetime'],received_at=NOW.isoformat(),
                   source_url='https://statsapi.mlb.com/api/v1.1/game/1/feed/live',response_sha256='a'*64)
    def mlb(rows,**kw):
        assert 'padding' not in str(rows) and len(json.dumps(rows))<1000
        assert rows[0]['request_eligibility']=='cached_future_fixture'
        assert 'status' not in rows[0]  # schedule request != proof of a pregame feed
        assert kw['request_limit']==1 and kw['slot_seconds']==300
        assert kw['request_timeout']==(2,3) and kw['budget_seconds']==6
        calls.append('mlb');return {'1':source}
    def kbo(old,**kw):
        assert kw['request_limit']==2 and kw['budget_seconds']==6
        calls.append('kbo');return dict(version='test',seed_hash='s',observed_at=NOW.isoformat(),
            daily_states={'2026-10-30':{'status':'available'}},calendar_cache={})
    monkeypatch.setattr(mlb_candidate_feed,'collect',mlb)
    monkeypatch.setattr(kbo_candidate_source,'collect',kbo)
    original=db.get_document
    def read(name):
        assert name!='player_info','full document read forbidden'
        return original(name)
    monkeypatch.setattr(db,'get_document',read)
    result=run_once(db=db,clock=lambda:NOW)
    assert result['mlb_captured']==1 and calls==['mlb','kbo']
    saved=db.get_document('mlb_model_candidate_inputs')['games'][0]
    assert saved['research_candidate_input']==source
    assert saved['updated_at']==NOW.isoformat()
    assert len(saved['lineups']['home'])==9
    assert run_once(db=db,clock=lambda:NOW+timedelta(minutes=1))['status']=='cooldown'
    assert calls==['mlb','kbo']
    run_once(db=db,clock=lambda:NOW+timedelta(minutes=5))
    assert calls==['mlb','kbo','mlb']  # KBO calendar retains a separate 25m budget.


def test_projection_rejects_naive_ambiguous_and_non_mlb_source_ids(tmp_path):
    db=RuntimeDatabase(tmp_path/'db.sqlite3');g=fixtures(db)
    db.put_document('player_info',dict(games=[g,g,dict(g,game_id='20261029TEAM'),
        dict(g,game_id='3',game_datetime='2026-10-29T15:00:00')]))
    assert _fixtures(db,NOW)==[]


def test_no_runtime_db_has_no_network_or_file_writes(monkeypatch):
    import runtime_db
    monkeypatch.setattr(runtime_db,'database_enabled',lambda:False)
    assert run_once()=={'status':'disabled_without_runtime_db'}


def test_kbo_source_failure_does_not_block_lightweight_mlb_retry(monkeypatch):
    import kbo_pregame,baseball_candidate_watch
    calls=[]
    monkeypatch.setattr(kbo_pregame,'collect_available',lambda:(_ for _ in ()).throw(requests.ReadTimeout()))
    monkeypatch.setattr(baseball_candidate_watch,'run_once',lambda:calls.append('watch') or {'status':'checked'})
    assert kbo_pregame.main()==1
    assert calls==['watch']


def test_failed_optional_watch_does_not_turn_healthy_kbo_collection_into_failure(monkeypatch):
    import kbo_pregame,baseball_candidate_watch
    monkeypatch.setattr(kbo_pregame,'collect_available',lambda:{'games':[],'errors':0,'generated_at':NOW.isoformat()})
    monkeypatch.setattr(kbo_pregame,'persist_document',lambda *a:None)
    monkeypatch.setattr(baseball_candidate_watch,'run_once',lambda:(_ for _ in ()).throw(ValueError('unavailable')))
    assert kbo_pregame.main()==0


def test_new_source_document_flows_into_actual_db_refresh_without_heavy_player_job(tmp_path,monkeypatch):
    import mlb_candidate_feed,kbo_candidate_source
    from live_market_refresh import refresh_document
    from prediction_runtime import ledger_features
    from test_live_market_refresh import _live_odds
    monkeypatch.setenv('PROODD_DB_PATH',str(tmp_path/'db.sqlite3'))
    db=RuntimeDatabase();g=fixtures(db)
    raw=feed()
    source=extract(raw,game_pk=1,scheduled_at=g['game_datetime'],received_at=NOW.isoformat(),
                   source_url='https://statsapi.mlb.com/api/v1.1/game/1/feed/live',response_sha256='a'*64)
    monkeypatch.setattr(mlb_candidate_feed,'collect',lambda *a,**kw:{'1':source})
    monkeypatch.setattr(kbo_candidate_source,'collect',lambda *a,**kw:dict(observed_at=NOW.isoformat(),daily_states={},calendar_cache={}))
    assert run_once(db=db,clock=lambda:NOW)['mlb_captured']==1
    odds=_live_odds();odds['generated_at']=NOW.isoformat()
    for row in odds['markets']['102'].values():
        row.update(league='MLB',home='Home',away='Away',date='10.30(금) 00:00')
    doc,changed=refresh_document({'live':[]},odds,now=NOW+timedelta(seconds=1))
    assert changed==1
    features=ledger_features(doc['live'][0])
    assert features['research_player_inputs']['candidate_input']==source
    assert _fixtures(db,NOW)[0]['gamePk']==1
