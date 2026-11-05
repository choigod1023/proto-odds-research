"""Synthetic source/index integration; no actual future-game verification."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json

from tests.test_m0_forward_provider import capture, query
from research.kbo_forward.server_cycle import publish_index
from src import m0_forward_provider as provider
from deploy import supervisor


def test_index_producer_provider_and_restart_recovery(capture):
    root,path,record,_,_,_=capture
    assert publish_index(path.parent)==1
    index=path.parent/'capture-index.json';before=index.read_bytes();mtime=index.stat().st_mtime_ns
    assert query(capture)['status']=='available'
    assert publish_index(path.parent)==1 and index.read_bytes()==before and index.stat().st_mtime_ns==mtime
    index.unlink()  # Own temporary test data; simulate interruption before pointer publication.
    assert publish_index(path.parent)==1 and index.read_bytes()==before
    assert query(capture)['capture_hash']==record['hash']


def test_public_game_fields_bind_to_the_verified_source_provider(capture,monkeypatch):
    import sys
    from src.multi_market_recommendations import build
    from src.ai_decision import annotate_options
    root,path,record,normal,now,_=capture
    publish_index(path.parent)
    monkeypatch.setattr(provider,'ROOT',root)
    monkeypatch.setitem(sys.modules,'m0_forward_provider',provider)
    start=datetime.fromisoformat(normal['kickoff_at']).astimezone(timezone(timedelta(hours=9)))
    g=dict(year=start.year,round=131,date=start.strftime('%m.%d(목) %H:%M'),league='KBO',sport='bs',
           home='삼성',away='KIA',status='경기전',live_market_observed_at=now.isoformat(),
           options=[dict(market='언더오버',label='U 8.5',line=8.5,게임번호='31',선택=s,배당=p,시장확률=q)
                    for s,p,q in [('언더',1.8,.55),('오버',2.,.45)]])
    annotate_options(g)
    out=build(g,[],now.isoformat(),now.isoformat())
    assert out['m0_status']=='available' and out['items']
    assert out['items'][0]['comparisons']['m0_actual']['capture_hash']==record['hash']


def test_server_job_uses_existing_slot_timeout_and_changed_index_publication(monkeypatch):
    job=next(j for j in supervisor.LOOPERS if j[0]=='KBO M0 사전예측')
    assert job[2]==300 and 'research/kbo_forward/server_cycle.py' in job[1]
    assert job[0] in supervisor.MEMORY_HEAVY_LOOPERS
    revisions=iter([None,(100,200)])
    monkeypatch.setattr(supervisor,'_tipster_revision',lambda name:next(revisions))
    import threading
    pending=threading.Event();monkeypatch.setattr(supervisor,'_tipster_publish_pending',pending)
    calls=[]
    monkeypatch.setattr(supervisor.subprocess,'run',lambda *a,**kw:(calls.append(kw) or SimpleNamespace(returncode=0)))
    published=[]
    monkeypatch.setattr(supervisor,'_run_market_refresh',lambda:(published.append(True) or SimpleNamespace(returncode=0)))
    assert supervisor._collect_tipster_and_publish(job[0],job[1])==0
    assert calls[0]['timeout']==180 and pending.is_set() and published==[True]
