import copy, importlib.util, sys
from datetime import timedelta
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import ledger as l
spec=importlib.util.spec_from_file_location('prior_forward_fixtures',l.OLD/'test_forward.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)

def payload(kind='full_game_3way'):
    x=old.snapshot()
    if kind=='full_game_3way':
        x['market'].update(kind=kind,settlement_rule='draw_is_outcome')
        x['market']['odds']['draw']='3.10'
    return x

def result(x,c,status='final',h=2,a=2):
    end=l.moment(x['event']['kickoff_at'])+timedelta(hours=4)
    body={'synthetic':True,'h':h,'a':a,'status':status}
    return {'event_key':c['event_key'],**{k:x['event'][k] for k in ('official_game_id','season','home_id','away_id')},'game_date':x['row']['event_date'],'status':status,'home_score':h,'away_score':a,'evidence':{'uri':'https://www.koreabaseball.com/test','received_at':end.isoformat(),'body':body,'sha256':l.digest(l.canonical(body))}},end

def test_same_draw_refunds_two_way_but_loses_three_way(tmp_path):
    outcomes=[]
    for kind in ('full_game_2way','full_game_3way'):
        x=payload(kind);p=tmp_path/(kind+'.jsonl')
        c=l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
        r,end=result(x,c);l.settle(r,p,clock=lambda:end)
        outcomes.append(l.summary(p,now=end)['groups'][kind]['models']['baseline'])
    assert outcomes[0]['roi']==0 and outcomes[0]['refunds']==1
    assert outcomes[1]['roi']==-1 and outcomes[1]['refunds']==0

def test_corrections_append_and_unchanged_economics_do_not_duplicate(tmp_path):
    x=payload();p=tmp_path/'x.jsonl';c=l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
    r,end=result(x,c,h=4,a=2);s=l.settle(r,p,clock=lambda:end);before=p.read_bytes()
    r['evidence']['received_at']=(end+timedelta(minutes=1)).isoformat()
    assert l.settle(r,p,clock=lambda:end+timedelta(minutes=1)) is None
    assert p.read_bytes()==before
    r,end=result(x,c,h=1,a=2);end+=timedelta(minutes=2);r['evidence']['received_at']=end.isoformat();correction=l.settle(r,p,clock=lambda:end)
    assert p.read_bytes().startswith(before)
    assert correction['type']=='correction' and correction['supersedes_hash']==s['hash']
    z=l.summary(p,now=end)
    assert z['paired_settled']==1 and z['corrections']==1
    assert z['groups']['full_game_3way']['models']['baseline']['net_units']=='-1'

def test_pregame_cutoff_and_product_rejection_never_create_records(tmp_path):
    x=payload();p=tmp_path/'x.jsonl';x['market']['odds'].pop('draw')
    with pytest.raises(ValueError,match='complete odds'):l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
    x=payload();late=l.moment(x['event']['kickoff_at'])-timedelta(minutes=29)
    with pytest.raises(ValueError,match='T-30'):l.capture(x,p,clock=lambda:late,infer=old.fake_infer)
    assert not p.exists()

def test_unpriced_forecast_never_counts_in_roi_or_has_invented_price(tmp_path):
    x=payload();x.pop('market');x['evidence'].pop('odds');p=tmp_path/'x.jsonl'
    c=l.capture_unpriced(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
    assert 'market' not in c['input'] and 'odds' not in c['input']['evidence']
    assert c['eligible_for_paired_paper_comparison'] is False
    assert l.capture_unpriced(x,p,clock=lambda:old.NOW,infer=old.fake_infer) is None
    s=l.summary(p)
    assert s['unpriced_paired_forecasts']==1 and s['captured']==0 and s['groups']=={}

def test_first_complete_capture_remains_frozen_and_chain_tamper_rejected(tmp_path):
    x=payload();p=tmp_path/'x.jsonl';l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer);before=p.read_bytes()
    x['market']['odds']['home']='5.20'
    assert l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer) is None
    assert p.read_bytes()==before
    p.write_bytes(before.replace(b'"market_id":"test-only"',b'"market_id":"changed"'))
    with pytest.raises(ValueError,match='chain differs'):l.read(p)
