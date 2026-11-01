"""Actual DB/ledger recovery, not presentation-only unlinked fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import json


def test_new_sports_enter_real_picks_and_ledger_without_touching_frozen_game(tmp_path,monkeypatch):
    import src.live_market_refresh as refresh
    from src.runtime_db import RuntimeDatabase
    from src.prediction_runtime import PredictionRuntime
    from src.match_api import MatchViews

    monkeypatch.setenv('PROODD_DB_PATH',str(tmp_path/'runtime.sqlite3'))
    monkeypatch.setattr(refresh,'PREDICTION_LEDGER',tmp_path/'pregame.jsonl')
    stamp='2026-11-01T03:20:00+00:00';clock=datetime(2026,11,1,3,25,tzinfo=timezone.utc)
    real_refresh=refresh.refresh_document
    monkeypatch.setattr(refresh,'refresh_document',lambda d,o:real_refresh(d,o,now=clock))
    monkeypatch.setattr(refresh,'PredictionRuntime',lambda path:PredictionRuntime(path,clock=lambda:clock))
    frozen=dict(year=2026,round=129,sport='sc',league='K리그1',home='old-home',away='old-away',
                date='11.01(일) 11:00',status='경기전',options=[{'market':'승무패','선택':'홈','배당':1.7}],
                decision_snapshot={'action':'observe','as_of':'2026-11-01T00:00:00+00:00'})
    db=RuntimeDatabase();db.store_artifact('picks_v2',dict(generated_at='2026-10-30T10:13:55+00:00',live=[deepcopy(frozen)],past=[]))
    fixtures=[('bs','KBO','삼성','KIA'),('bk','KBL','창원LG','원주DB'),('vl','KOVO남','KB손보','삼성화재')]
    rows={}
    for i,(sport,league,home,away) in enumerate(fixtures):
        base=dict(sport=sport,league=league,home=home,away=away,date='11.01(일) 14:00',result='경기전')
        rows[str(i*2+1)]={**base,'market':'승패','label':'','n_way':2,'game_no':str(i*2+1),'odds':[1.73,1.75]}
        rows[str(i*2+2)]={**base,'market':'언더오버','label':'U 8.5' if sport=='bs' else 'U 160.5',
                         'n_way':2,'game_no':str(i*2+2),'odds':[1.65,1.84]}
    feed=dict(generated_at=stamp,rounds=[129],markets={'129':rows})
    db.store_artifact('live_odds',feed)
    assert refresh.refresh_once()==0
    recovered=db.get_artifact('picks_v2')
    assert recovered['generated_at']==stamp and len(recovered['live'])==4
    assert recovered['live'][0]==frozen
    for sport,league,home,away in fixtures:
        game=next(g for g in recovered['live'] if g.get('sport')==sport)
        assert game['status']=='경기전' and game['prediction_status']=='recorded_pregame'
        assert {x['market'] for x in game['options']}=={'승패','언더오버'}
        assert game['prediction_record']['selection'] in ('홈','원정')
        assert game['prediction_revision_id']
    ledger=PredictionRuntime(tmp_path/'pregame.jsonl',clock=lambda:clock).records()
    assert len(ledger)==3
    assert all(r['record_type']=='prediction' and r['kickoff']>'2026-11-01T03:25' for r in ledger)
    views=MatchViews(db)
    body=json.loads(views.get_bytes('all',compressed=False))
    assert {g['sport'] for g in body['live']} >= {'bs','bk','vl'}
    # Re-running the same saved feed is idempotent for immutable predictions.
    hashes=[r['record_hash'] for r in ledger]
    assert refresh.refresh_once()==0
    assert [r['record_hash'] for r in PredictionRuntime(tmp_path/'pregame.jsonl').records()]==hashes
