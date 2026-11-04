"""Synthetic receipt/capture integration; not proof of real pregame availability."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.kbo_forward.prices import DETAIL_URL,LIST_URL
from research.m1_service.alternative_observer import attach
from research.m1_service.observer import _append,canonical,digest,read


def sample(tmp_path):
    data=json.loads(gzip.decompress(Path('experiments/kbo-alternative-prices-20261104/detail-2026-48.body.gz').read_bytes()))
    s=data['compSchedules']; k=s['keys']
    r=next(dict(zip(k,x)) for x in s['datas'] if dict(zip(k,x)).get('matchSeq')==760)
    r['protoStatus']='2';s['datas']=[[r[x] for x in k]]
    start=datetime.fromtimestamp(r['gameDate']/1000,timezone.utc);now=start-timedelta(hours=1)
    info={'gmId':'G101','gmTs':'260048','gmOsidTsYear':2026,'saleStartDate':(now-timedelta(hours=1)).timestamp()*1000,'saleEndDate':start.timestamp()*1000}
    root=tmp_path/'prices';root.mkdir()
    records=[]
    for url,body,params in [(LIST_URL,{'protoGames':[info]},{}),(DETAIL_URL,data,{'gmId':'G101','gmTs':'260048','gameYear':'2026'})]:
        raw=canonical(body);sha=digest(raw);p=root/(sha+'.body');p.write_bytes(raw)
        t=now-timedelta(seconds=2 if url==LIST_URL else 0)
        records.append({'url':url,'params':params,'method':'POST','status':200,'content_type':'application/json','requested_at':(t-timedelta(seconds=1)).isoformat(),'received_at':t.isoformat(),'sha256':sha,'body_file':str(p)})
    event={'official_game_id':'official-GID','season':2026,'home_id':r['homeId'],'away_id':r['awayId'],'kickoff_at':start.isoformat()}
    forecasts={name:{'fixture_id':'normalized-row-ID','home_rate':4.5,'away_rate':4.,'probabilities_3way':{'home':.48,'draw':.1,'away':.42}} for name in ('m0_actual','m0_projected','m1_projected')}
    # Stub an already-verified capture. Capture source verification is tested in
    # test_m1_service_source_binding; this test isolates the *new* price binding.
    payload={'event':event,'row':{'fixture_id':'normalized-row-ID'},'evidence':{'odds':{'raw_sha256':records[1]['sha256'],'round_listing_sha256':records[0]['sha256'],'received_at':now.isoformat()}},
             'market':{'market_id':'G101:260048:759','kind':'full_game_2way','settlement_rule':'draw_void_refund','odds':{'home':1.2,'away':3.},'close_at':start.isoformat(),'round_close_at':start.isoformat()}}
    rec=SimpleNamespace(path=tmp_path/'ledger.jsonl',price_root=root,clock=lambda:now)
    capture=_append(rec.path,lambda rows:{'type':'capture','captured_at':now.isoformat(),'input':payload,'models':forecasts,'choices':{n:'home' for n in forecasts},'roi_eligible':True})
    return rec,capture,records,now


def test_same_received_response_wires_totals_to_all_models_once(tmp_path):
    rec,c,records,now=sample(tmp_path)
    result=attach(rec,c,records,now)
    assert result['recorded'] and result['offer_count']==2
    row=read(rec.path)[-1]
    assert row['type']=='alternative_review' and row['capture_hash']==c['hash']
    assert all(len(r['candidates'])==4 and r['automatic_replacement'] is None for r in row['reviews'].values())
    assert row['offers'][0]['fixture_id']=='normalized-row-ID'
    assert row['offers'][0]['official_game_id']=='official-GID'
    assert row['realized_roi'] is None
    rec.clock=lambda:now+timedelta(seconds=1)
    assert attach(rec,c,records,now+timedelta(seconds=1))['recorded'] is False
    assert len(read(rec.path))==2


@pytest.mark.parametrize('defect',['bytes','receipt','method','unbound','price_sha','season','late','stored_capture'])
def test_invalid_source_never_adds_candidate_review(tmp_path,defect):
    rec,c,records,now=sample(tmp_path)
    if defect=='bytes':Path(records[1]['body_file']).write_bytes(b'changed')
    elif defect=='receipt':records[1]['received_at']=(now+timedelta(seconds=1)).isoformat()
    elif defect=='method':records[1]['method']='GET'
    elif defect=='unbound':records[1]['body_file']=str(tmp_path/'elsewhere.body')
    elif defect=='price_sha':records[1]['sha256']='0'*64
    elif defect=='season':records[1]['params']['gameYear']='2025'
    elif defect=='late':now+=timedelta(minutes=31)
    else:c['choices']['m0_actual']='away'
    with pytest.raises((ValueError,FileNotFoundError)):attach(rec,c,records,now)
    assert len(read(rec.path))==1


def test_competing_round_suppresses_total_candidates(tmp_path):
    rec,c,records,now=sample(tmp_path)
    body=json.loads(Path(records[1]['body_file']).read_bytes());body['gmTs']='260049'
    raw=canonical(body);sha=digest(raw);path=rec.price_root/(sha+'.body');path.write_bytes(raw)
    other=deepcopy(records[1]);other.update(sha256=sha,body_file=str(path));other['params']['gmTs']='260049'
    result=attach(rec,c,records+[other],now)
    assert result['offer_count']==0 and result['excluded'][-1]['reason']=='multiple_round_candidates'


def test_no_extra_requests_and_late_processing_keeps_capture(tmp_path):
    rec,c,records,now=sample(tmp_path)
    rec.clock=lambda:now+timedelta(minutes=31)
    with pytest.raises(ValueError,match='finished after cutoff'):attach(rec,c,records,now)
    assert len(read(rec.path))==1


def test_resolved_timeout_retry_does_not_drop_review(tmp_path):
    rec,c,records,now=sample(tmp_path)
    failed={'url':DETAIL_URL,'method':'POST','params':deepcopy(records[1]['params']),'error_type':'ReadTimeout','failed_at':now.isoformat()}
    assert attach(rec,c,[records[0],failed,records[1]],now)['offer_count']==2


def test_unresolved_other_round_timeout_is_not_claimed_unique(tmp_path):
    rec,c,records,now=sample(tmp_path)
    failed={'url':DETAIL_URL,'method':'POST','params':{'gmTs':'260049'},'error_type':'ReadTimeout','failed_at':now.isoformat()}
    with pytest.raises(ValueError,match='unresolved_price_round'):attach(rec,c,records+[failed],now)
    assert len(read(rec.path))==1


@pytest.mark.parametrize('deadline',['t30','sale'])
def test_clock_rechecked_after_review_and_inside_append(tmp_path,monkeypatch,deadline):
    from research.m1_service import alternative_observer as module
    rec,c,records,now=sample(tmp_path)
    current=[now];rec.clock=lambda:current[0]
    if deadline=='sale':
        # An already-validated capture may have a sale close earlier than T-30.
        c['input']['market']['close_at']=(now+timedelta(seconds=1)).isoformat()
        p=rec.path;p.write_bytes(b'');c=_append(p,lambda rows:{k:v for k,v in c.items() if k not in ('hash','previous_hash')})
    real=module.review
    def slow(*args):
        result=real(*args);current[0]=now+timedelta(minutes=31) if deadline=='t30' else now+timedelta(seconds=2)
        return result
    monkeypatch.setattr(module,'review',slow)
    with pytest.raises(ValueError,match='finished after cutoff'):attach(rec,c,records,now)
    assert len(read(rec.path))==1
