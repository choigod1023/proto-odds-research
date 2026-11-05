import importlib.util
from pathlib import Path
p=Path(__file__).with_name('audit.py'); spec=importlib.util.spec_from_file_location('baseline_audit',p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def sample():
    g=dict(mchScore='3:1',gameResult='0',protoStatus='4',gameReject='0',matchSeq=1)
    r=dict(itemCode='SC',winHandi=-2,loseHandi=2,winAllot=2.1,drawAllot=2.2,loseAllot=2.5,gameResult='1',protoStatus='4',gameReject='0',gameSubject=None,matchSeq=2)
    return r,g

def test_integer_three_way_draw_and_exact_code():
    r,g=sample(); result,error=m.evaluate(r,[g],'handicap')
    assert error is None and result['result']==1 and result['pick']==0 and not result['win']
    r['gameResult']='0'; assert m.evaluate(r,[g],'handicap')[1]=='result_code_mismatch'

def test_total_and_period_identity_fail_closed():
    r,g=sample(); r.update(winHandi=3.5,loseHandi=3.5,drawAllot=0,gameResult='2')
    assert m.evaluate(r,[g],'total')[0]['result']==2
    r['winHandi']=3.25; assert m.evaluate(r,[g],'total')[1]=='invalid_or_quarter_line'
    assert ('SC','121','7') not in m.PRODUCTS
    assert m.evaluate(r,[],'total')[1]=='general_not_unique'

def test_two_way_half_and_general_result():
    r,g=sample(); r.update(itemCode='BS',winHandi=-1.5,loseHandi=1.5,drawAllot=0,gameResult='0')
    assert m.evaluate(r,[g],'handicap')[0]['win']
    r['winHandi']=-1; r['loseHandi']=1
    assert m.evaluate(r,[g],'handicap')[1]=='unsupported_twoway_line'
    g['gameResult']='2'; assert m.evaluate(r,[g],'handicap')[1]=='general_result_code_mismatch'

def test_metrics_chronological_drawdown():
    rows=[dict(kickoff=str(i),fixture=str(i),round=1,matchSeq=i,win=w,price=2) for i,w in enumerate([False,True,False,False])]
    x=m.metrics(rows); assert x['n']==4 and x['wins']==1 and x['net_units']==-2 and x['max_drawdown']==2


def test_baseball_draw_general_refund_does_not_refund_handicap_or_total():
    r,g=sample(); g.update(mchScore='5:5',gameResult='4'); r.update(itemCode='BS',winHandi=-2.5,loseHandi=2.5,drawAllot=0,gameResult='2')
    result,error=m.evaluate(r,[g],'handicap')
    assert error is None and result['result']==2 and not result['win']
    r.update(winHandi=9.5,loseHandi=9.5,gameResult='2')
    assert m.evaluate(r,[g],'total')[0]['result']==2
    g['gameResult']='1'; assert m.evaluate(r,[g],'total')[1]=='general_result_code_mismatch'
    r['itemCode']='BK'; assert m.evaluate(r,[g],'total')[1]=='basketball_general_draw'

def test_conflicting_round_product_identity():
    first={'matchSeq':9,'homeId':'A'}; same=dict(first); other={'matchSeq':9,'homeId':'B'}
    assert m.conflicting_sequences([first,same])==set()
    assert m.conflicting_sequences([first,other])=={'9'}
