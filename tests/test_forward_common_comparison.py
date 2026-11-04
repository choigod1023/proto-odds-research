"""Synthetic lifecycle and failure tests; no claim of live pregame capture."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone

import pytest

from research.kbo_forward.common_comparison import capture_pair,settle_pairs,summary
from research.m1_service.observer import canonical,digest,read

NOW=datetime(2026,11,6,8,tzinfo=timezone.utc)


def fixture():
    event={'official_game_id':'20261106KTSS0','season':2026,'home_id':'SS','away_id':'KT',
           'kickoff_at':(NOW+timedelta(hours=1,minutes=30)).isoformat()}
    cap={'hash':'a'*64,'captured_at':NOW.isoformat(),'input':{'event':event,'market':{'market_id':'G101:260130:100'}}}
    result={'status':'eligible','official_game_id':event['official_game_id'],'market_id':'G101:260130:100',
            'operating_record_hash':'b'*64,'operating_as_of':(NOW+timedelta(minutes=5)).isoformat(),
            'shared_prices':{'home':1.8,'away':1.9},'three_selections':{'m0_actual':'home','operating_vote':'away','market':'home'}}
    return cap,result


def captured(path):
    c,r=fixture();capture_pair(path,c,{'export_sha256':'d'*64},NOW+timedelta(minutes=10),matcher=lambda *args:r)
    return c


def settlement(c,*,home=3,away=1,when=None,cancelled=False):
    when=when or NOW+timedelta(hours=5)
    g={'G_ID':c['input']['event']['official_game_id'],'HOME_ID':'SS','AWAY_ID':'KT','SEASON_ID':2026,'G_DT':'20261106',
       'GAME_STATE_SC':'3','GAME_RESULT_CK':'1','CANCEL_SC_ID':'1' if cancelled else '0','CANCEL_SC_NM':'우천취소' if cancelled else '',
       'B_SCORE_CN':home,'T_SCORE_CN':away}
    body={'game':[g]};ev={'uri':'https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList',
                         'received_at':when.isoformat(),'body':body,'sha256':digest(canonical(body))}
    return {'type':'settlement','hash':'s'*64,'capture_hash':c['hash'],'evidence':ev,
            'economics':{'status':'cancelled' if cancelled else 'final','home_score':home,'away_score':away}}


def test_same_snapshot_pricing_and_immutable_first_common_sample(tmp_path):
    path=tmp_path/'common.jsonl';c,r=fixture();match=lambda *args:r
    out=capture_pair(path,c,{},NOW+timedelta(minutes=10),matcher=match)
    assert out['recorded'] and summary(path)['captured']==1
    r['operating_record_hash']='c'*64
    assert capture_pair(path,c,{},NOW+timedelta(minutes=11),matcher=match)['recorded'] is False
    assert len(read(path))==1 and read(path)[0]['operating_record_hash']=='b'*64


@pytest.mark.parametrize('bad',['late','future_operating','m0_after_operating','wrong_fixture','wrong_market','wrong_side','bad_price'])
def test_no_invalid_or_late_common_capture(tmp_path,bad):
    c,r=fixture();now=NOW+timedelta(minutes=10)
    if bad=='late':now=NOW+timedelta(minutes=61)
    elif bad=='future_operating':r['operating_as_of']=(now+timedelta(seconds=1)).isoformat()
    elif bad=='m0_after_operating':r['operating_as_of']=(NOW-timedelta(seconds=1)).isoformat()
    elif bad=='wrong_fixture':r['official_game_id']='other'
    elif bad=='wrong_market':r['market_id']='G101:260130:101'
    elif bad=='wrong_side':r['three_selections']['market']='draw'
    else:r['shared_prices']['away']=float('nan')
    with pytest.raises(ValueError):capture_pair(tmp_path/'c.jsonl',c,{},now,matcher=lambda *a:r)


def test_excluded_bridge_is_not_recorded(tmp_path):
    c,_=fixture();out=capture_pair(tmp_path/'x',c,{},NOW,matcher=lambda *a:{'status':'excluded','reason':'source_missing'})
    assert not out['recorded'] and not (tmp_path/'x').exists()


def test_same_price_three_way_lifecycle_and_official_correction(tmp_path):
    path=tmp_path/'common';c=captured(path);r=settlement(c);now=NOW+timedelta(hours=5)
    assert len(settle_pairs(path,[r],now))==1
    scores=summary(path)['methods']
    assert scores['m0_actual']['net_units']=='0.8' and scores['market']['net_units']=='0.8'
    assert scores['operating_vote']['net_units']=='-1'
    assert not settle_pairs(path,[r],now)
    corrected=settlement(c,home=1,away=3,when=now+timedelta(hours=1));corrected['hash']='t'*64
    assert len(settle_pairs(path,[corrected],now+timedelta(hours=1)))==1
    scores=summary(path)['methods'];assert scores['operating_vote']['net_units']=='0.9' and scores['m0_actual']['net_units']=='-1'
    with pytest.raises(ValueError,match='backwards'):settle_pairs(path,[r],now+timedelta(hours=2))
    assert summary(path)['settled']==1 and summary(path)['formal_promotion_eligible'] is False


@pytest.mark.parametrize('cancelled',[True,False])
def test_cancellation_and_draw_refund_without_fake_hits(tmp_path,cancelled):
    path=tmp_path/'c';c=captured(path);now=NOW+timedelta(minutes=20) if cancelled else NOW+timedelta(hours=5)
    r=settlement(c,home=2,away=2,when=now,cancelled=cancelled)
    settle_pairs(path,[r],now)
    assert all(s['net_units']=='0' and s['decided']==0 for s in summary(path)['methods'].values())


def test_tampered_official_body_rejected(tmp_path):
    path=tmp_path/'c';c=captured(path);r=settlement(c);r['evidence']['body']['game'][0]['B_SCORE_CN']=5
    with pytest.raises(ValueError,match='source'):settle_pairs(path,[r],NOW+timedelta(hours=5))


def test_processing_and_lock_delay_cannot_backdate_common_capture(tmp_path):
    c,r=fixture()
    with pytest.raises(ValueError,match='finished after cutoff'):
        capture_pair(tmp_path/'c',c,{},NOW+timedelta(minutes=10),matcher=lambda *a:r,
                     clock=lambda:NOW+timedelta(minutes=61))
    assert not read(tmp_path/'c')


def test_reconcile_idle_and_source_failure_never_lose_m0(tmp_path,monkeypatch):
    from research.m1_service import observer
    from research.kbo_forward.common_comparison import reconcile
    from tests.test_m1_service_runtime import packet,test_validator
    monkeypatch.setattr(observer,'_verify_archives',lambda *a:None)
    monkeypatch.setattr(observer,'_verify_market_bytes',lambda *a:None)
    p,now=packet();recorder=observer.Recorder(tmp_path/'predictions.jsonl',test_validator,clock=lambda:now)
    calls=[]
    def unavailable(*args):
        calls.append(args)
        raise TimeoutError('synthetic source unavailable')
    assert reconcile(recorder,tmp_path,now,source_fn=unavailable)['bridge_attempts']==[]
    assert not calls
    recorder.capture(p);before=recorder.path.read_bytes()
    status=reconcile(recorder,tmp_path,now,source_fn=unavailable)
    assert status['bridge_attempts'][0]['status']=='source_unavailable'
    assert status['captured']==0 and recorder.path.read_bytes()==before and len(calls)==1
    assert calls[0][1][0]['market_id']==p['market']['market_id']
    late=observer.moment(p['event']['kickoff_at'])-timedelta(minutes=29)
    assert reconcile(recorder,tmp_path,late,source_fn=unavailable)['bridge_attempts']==[]
    assert len(calls)==1


def test_reconcile_capture_restart_and_official_settlement(tmp_path,monkeypatch):
    from research.m1_service import observer
    from research.kbo_forward import operating_vote_bridge as bridge
    from research.kbo_forward.common_comparison import reconcile
    from tests.test_m1_service_runtime import packet,test_validator,final_result
    monkeypatch.setattr(observer,'_verify_archives',lambda *a:None)
    monkeypatch.setattr(observer,'_verify_market_bytes',lambda *a:None)
    p,now=packet();recorder=observer.Recorder(tmp_path/'predictions.jsonl',test_validator,clock=lambda:now)
    m0=recorder.capture(p)
    def source(config,events,when):return {'export_sha256':'e'*64}
    def match(capture,export,when):
        return {'status':'eligible','official_game_id':p['event']['official_game_id'],
                'market_id':p['market']['market_id'],'operating_record_hash':'b'*64,
                'operating_as_of':now.isoformat(),'shared_prices':{'home':1.8,'away':2.1},
                'three_selections':{'m0_actual':m0['choices']['m0_actual'],'operating_vote':'away','market':'home'}}
    monkeypatch.setattr(bridge,'match_operating',match)
    first=reconcile(recorder,tmp_path,now,source_fn=source)
    assert first['captured']==1 and first['settled']==0
    before=(tmp_path/'common-ledger.jsonl').read_bytes()
    assert reconcile(recorder,tmp_path,now,source_fn=lambda *a:pytest.fail('duplicate network request'))['captured']==1
    assert (tmp_path/'common-ledger.jsonl').read_bytes()==before
    end=observer.moment(p['event']['kickoff_at'])+timedelta(hours=3);recorder.clock=lambda:end
    recorder.settle(final_result(p,end,4,1))
    result=reconcile(recorder,tmp_path,end,source_fn=lambda *a:pytest.fail('post-cutoff request'))
    assert result['settled']==1
    assert result['methods']['operating_vote']['net_units']=='-1'
    assert result['methods']['market']['net_units']=='0.8'
    assert result['paper_wallet_changed'] is False and not result['formal_promotion_eligible']
