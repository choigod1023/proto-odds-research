import time
import requests
import live_scores as ls


def test_named_current_runners_and_players_are_not_starters():
    raw={"gameStatus":"IN_PROGRESS","period":3,"inningDivision":"BOTTOM",
        "firstBaseOccupied":True,"firstBaseOccupiedBatter":{"id":4250,"name":" J.P 크로포드 "},
        "secondBaseOccupied":False,"secondBaseOccupiedBatter":{"name":"old runner"},
        "thirdBaseOccupied":True,"thirdBaseOccupiedBatter":None,
        "ball":0,"strike":2,"out":1,"currentBatter":{"name":"타자"},"currentPitcher":{"name":"구원 투수"}}
    result=ls.named_baseball_situation(raw)
    assert result['bases']['first']=={'occupied':True,'runner':'J.P 크로포드','runner_id':4250}
    assert result['bases']['second']['runner'] is None
    assert result['bases']['third']['occupied'] is True
    assert result['bases']['third']['runner'] is None
    assert result['balls']==0 and result['pitcher']=='구원 투수'
    assert result['situation_inning']==3
    raw['gameStatus']='FINAL'
    assert ls.named_baseball_situation(raw)=={}


def test_missing_occupancy_is_not_empty_base():
    result=ls.named_baseball_situation({'gameStatus':'IN_PROGRESS','ball':True,'teams':{'home':{'startPitcher':{'name':'선발'}}}})
    assert result=={}


def test_enrichment_fetches_named_and_falls_back_after_naver_failure(monkeypatch):
    monkeypatch.setattr(ls,'_situation_job',lambda *args:{})
    calls=[]
    def fetch(game,deadline):
        calls.append(game['named_game_id'])
        return {'bases':{'first':{'occupied':True,'runner':'주자'}},'situation_observed_at':'new'}
    monkeypatch.setattr(ls,'_named_situation_job',fetch)
    games=[{'source':source,'status':'STARTED','league':'MLB','game_id':f'{source}:123','named_game_id':'123','observed_at':'old'} for source in ('named','naver')]
    ls.enrich_situations(games)
    assert calls==['123','123']
    assert all(g['bases']['first']['runner']=='주자' and g['observed_at']=='old' for g in games)


def test_expired_budget_never_calls_detail(monkeypatch):
    monkeypatch.setattr(ls,'_session',lambda:(_ for _ in ()).throw(AssertionError('network called')))
    assert ls._named_situation_job({'game_id':'named:123'},time.monotonic()-1)=={}


def test_dedup_keeps_named_detail_identity_for_naver_fallback():
    base={'league':'MLB','start':'2026-09-06T10:00:00+09:00','home':'홈','away':'원정','status':'STARTED'}
    rows=ls.deduplicate_games([{**base,'source':'named','game_id':'named:123','named_game_id':'123'},
                               {**base,'source':'naver','game_id':'naver:abc'}])
    assert len(rows)==1
    assert rows[0]['source']=='naver'
    assert rows[0]['named_game_id']=='123'
