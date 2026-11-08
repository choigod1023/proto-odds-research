import gzip
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest

ROOT=Path(__file__).resolve().parents[1]/'experiments/kbl-history-20261108e'
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
r=load('run'); c=load('collect')


def test_previous_ten_require_complete_history_and_four_day_lag():
    from datetime import date,timedelta
    base=date(2025,1,1); games=[]
    for team in ('a','b'):
        for i in range(10):
            games.append({'id':team+str(i),'day':str(base+timedelta(days=i)),
                          'home':team,'away':'z','net':[float(i),float(-i)]})
    assert r.features('2025-01-14','a','b',games,games)==0
    assert r.features('2025-01-13','a','b',games,games) is None
    assert r.features('2025-01-14','a','b',games[:-1],games) is None
    earlier=r.features('2025-01-14','a','b',games,games)
    future={'id':'future','day':'2025-01-13','home':'a','away':'z','net':[999,-999]}
    assert r.features('2025-01-14','a','b',games+[future],games+[future])==earlier


def test_box_time_points_and_attempt_checks():
    f={'payload':{'home_team_id':'1','away_team_id':'2'},'kickoff':'2025-01-01T00:00:00Z'}
    final={'payload':{'home_score':2,'away_score':3}}
    def p(i,team,score):
        return {'player':{'pcode':i,'tcode':team},'records':{'playMin':200,'playSec':0,'score':score,
                'fgtA':3,'fgA':2,'threepA':1,'offr':0,'to':0,'ftA':0}}
    data=[p('a','1',2),p('b','2',3)]
    assert r.box(f,final,json.dumps(data).encode())['ot_periods']==0
    data[0]['records']['fgtA']=4
    with pytest.raises(ValueError,match='attempt_definition'): r.box(f,final,json.dumps(data).encode())


def test_capture_cache_reuse_and_hash_rejection(tmp_path):
    class Response(io.BytesIO):
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    calls=[]
    def opener(req,timeout):
        calls.append(req.full_url); return Response(b'[{"test":1}]')
    assert c.capture('S45G01N116',tmp_path,opener,lambda _:None)=='downloaded'
    assert c.capture('S45G01N116',tmp_path,opener,lambda _:None)=='cached'
    assert len(calls)==1
    (tmp_path/'S45G01N116.body.gz').write_bytes(gzip.compress(b'[]'))
    with pytest.raises(ValueError,match='hash'): c.capture('S45G01N116',tmp_path,opener,lambda _:None)


def test_http_403_stops_without_retry(tmp_path):
    calls=[]
    def opener(req,timeout):
        calls.append(1); raise urllib.error.HTTPError(req.full_url,403,'forbidden',{},None)
    with pytest.raises(PermissionError): c.capture('S45G01N116',tmp_path,opener,lambda _:None)
    assert len(calls)==1


def test_temporary_failure_has_backoff_and_checkpoints(tmp_path):
    class Response(io.BytesIO):
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    calls=[]; sleeps=[]
    def opener(req,timeout):
        calls.append(1)
        if len(calls)==1: raise urllib.error.HTTPError(req.full_url,429,'throttled',{},None)
        return Response(b'[{"ok":1}]')
    assert c.capture('S45G01N116',tmp_path,opener,sleeps.append)=='downloaded'
    assert len(calls)==2 and sleeps==[3]
    assert (tmp_path/'S45G01N116.json').exists()


def test_exhausted_429_stops_before_next_game_and_saves_deadline(tmp_path,monkeypatch):
    calls=[]; sleeps=[]; real_capture=c.capture
    def opener(req,timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url,429,'throttled',{},None)
    monkeypatch.setattr(c,'game_ids',lambda:['S45G01N116','S45G01N117'])
    monkeypatch.setattr(c,'capture',lambda game,folder:real_capture(game,folder,opener,sleeps.append))
    with pytest.raises(c.RateLimitError): c.run_batch(tmp_path,2)
    saved=json.loads((tmp_path/'rate-limit.json').read_text(encoding='utf8'))
    assert len(calls)==3 and all('S45G01N116' in u for u in calls)
    assert sleeps==[3,6] and saved['game_id']=='S45G01N116' and saved['next_eligible']


def test_offset_fit_and_profit_math():
    rows=[{'market':.5,'x':1.,'y':1} for _ in range(20)]
    assert r.fit_beta(rows)>0
    assert abs(r.fit_beta([dict(x=0.,market=.5,y=1)]) ) < 1e-5
    predictions=[{'day':'2026-01-01','y':1,'odds':[1.5,2.4],'prob':{'candidate':.6}},
                 {'day':'2026-01-02','y':0,'odds':[1.5,2.4],'prob':{'candidate':.6}}]
    metrics=r.metrics(predictions,'candidate')
    assert metrics['accuracy']==.5 and metrics['conditional_roi']==pytest.approx(-.25)
    assert metrics['daily_mdd_units']==pytest.approx(1.)


def test_retry_after_header_causes_immediate_stop_and_deadline(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    calls=[]; real_capture=c.capture
    def opener(req,timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url,429,'throttled',{'Retry-After':'180'},None)
    monkeypatch.setattr(c,'game_ids',lambda:['S45G01N116','S45G01N117'])
    monkeypatch.setattr(c,'capture',lambda game,folder:real_capture(game,folder,opener,lambda _:None))
    with pytest.raises(c.RateLimitError): c.run_batch(tmp_path,2)
    saved=json.loads((tmp_path/'rate-limit.json').read_text(encoding='utf8'))
    assert len(calls)==1 and saved['retry_after']=='180'
    assert (datetime.fromisoformat(saved['next_eligible'])-datetime.fromisoformat(saved['at'])).total_seconds()==180
    with pytest.raises(c.RateLimitError): c.run_batch(tmp_path,2)
    assert len(calls)==1
