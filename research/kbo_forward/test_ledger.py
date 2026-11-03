import copy, importlib.util, sys
from datetime import timedelta
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import ledger as l
spec=importlib.util.spec_from_file_location('prior_forward_fixtures',l.OLD/'test_forward.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)

def payload(kind='full_game_3way'):
    x=old.snapshot();x['market']['close_at']=x['event']['kickoff_at'];x['market']['round_close_at']=(l.moment(x['event']['kickoff_at'])+timedelta(hours=4)).isoformat()
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
    assert l.settle(r,p,clock=lambda:end+timedelta(minutes=1))['type']=='result_confirmation'
    assert p.read_bytes().startswith(before)
    before=p.read_bytes()
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


def test_official_codes_reproduce_frozen_training_team_coefficients():
    row=old.snapshot()['row'];original=copy.deepcopy(row)
    assert (row['home_id'],row['away_id'])==('삼성','KIA')
    expected,_=l.f.forecast(row);row.update(home_id='SS',away_id='HT')
    got,_=l.infer_frozen(row)
    for model in l.MODELS:
        assert got[model]['probabilities_3way']==pytest.approx(expected[model]['probabilities_3way'])
    assert (row['home_id'],row['away_id'])==('SS','HT')


def test_unpriced_accuracy_settles_without_inventing_roi(tmp_path):
    x=payload();x.pop('market');x['evidence'].pop('odds');path=tmp_path/'x.jsonl'
    c=l.capture_unpriced(x,path,clock=lambda:old.NOW,infer=old.fake_infer)
    r,end=result(x,c,h=3,a=1);done=l.settle(r,path,clock=lambda:end)
    assert done['type']=='unpriced_settlement' and 'paper_profits' not in done
    z=l.summary(path,now=end)
    assert z['unpriced_outcome_comparison']['baseline']=={'n':1,'hits':1,'roi':None}
    assert z['paired_settled']==0 and z['groups']=={}


def test_sale_closes_before_t30_or_during_inference_rejected(tmp_path):
    x=payload();x['market']['close_at']=(old.NOW-timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError,match='closed before'):l.capture(x,tmp_path/'x.jsonl',clock=lambda:old.NOW,infer=old.fake_infer)
    x=payload();x['market']['close_at']=(old.NOW+timedelta(seconds=1)).isoformat();ticks=iter([old.NOW,old.NOW+timedelta(seconds=2)])
    with pytest.raises(ValueError,match='closed during'):l.capture(x,tmp_path/'x.jsonl',clock=lambda:next(ticks),infer=old.fake_infer)
    assert not (tmp_path/'x.jsonl').exists()


def test_missing_prices_stay_in_preobserved_opportunity_denominator(tmp_path):
    x=payload();p=tmp_path/'x.jsonl';l.observe_opportunity(x['event'],p,{'test_only':True},clock=lambda:old.NOW)
    late=l.moment(x['event']['kickoff_at'])-timedelta(minutes=29)
    l.close_missed_opportunities(p,[{'official_game_id':x['event']['official_game_id'],'reason':'no verified price'}],clock=lambda:late)
    before=p.read_bytes();l.close_missed_opportunities(p,[],clock=lambda:late)
    assert p.read_bytes()==before
    s=l.summary(p,now=late)
    assert s['observed_pregame_opportunities']==1 and s['closed_without_priced_prediction']==1 and s['captured']==0


def test_newer_confirmation_rejects_older_conflicting_result(tmp_path):
    x=payload();p=tmp_path/'x.jsonl';c=l.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
    r,end=result(x,c,h=3,a=1);l.settle(r,p,clock=lambda:end)
    r['evidence']['received_at']=(end+timedelta(minutes=2)).isoformat();l.settle(r,p,clock=lambda:end+timedelta(minutes=2))
    conflict,_=result(x,c,h=1,a=3);conflict['evidence']['received_at']=(end+timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError,match='stale conflicting'):l.settle(conflict,p,clock=lambda:end+timedelta(minutes=3))
    assert l.summary(p)['corrections']==0


@pytest.mark.parametrize('defect',['missing_pair','nonfinite','wrong_identity'])
def test_unpriced_invalid_forecasts_cannot_enter_comparison(tmp_path,defect):
    x=payload();x.pop('market');x['evidence'].pop('odds')
    def wrong(row):
        p,h=old.fake_infer(row)
        if defect=='missing_pair':p.pop('equal_ensemble')
        elif defect=='nonfinite':p['equal_ensemble']['probabilities_3way']['home']=float('nan')
        else:p['equal_ensemble']['fixture_id']='another-game'
        return p,h
    with pytest.raises(ValueError):l.capture_unpriced(x,tmp_path/'x.jsonl',clock=lambda:old.NOW,infer=wrong)
    assert not (tmp_path/'x.jsonl').exists()
