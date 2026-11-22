import copy,hashlib,importlib.util,json,math,sys
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments'/'frozen-neighbor-engine-20261102'/'engine.py'
spec=importlib.util.spec_from_file_location('engine_test',P);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

@pytest.fixture
def bank(monkeypatch,tmp_path):
    rows=[]
    for i in range(228):
        day=date(2024,1,1)+timedelta(days=i)
        rows.append({'id':f'b{i:03}','day':day.isoformat(),'dc_training_cutoff':(day-timedelta(days=7)).isoformat(),'split':'meta_history_2024','market_p':[.4,.3,.3],'dc_p':[.5,.2,.3],'y':i%3})
    raw=''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows).encode();p=tmp_path/'bank.jsonl';p.write_bytes(raw)
    monkeypatch.setattr(m,'BANK_SHA',hashlib.sha256(raw).hexdigest());return m.load_bank(p)

def target(i=0):
    d=date(2026,3,10)+timedelta(days=i*8)
    return {'id':f't{i}','eligibility':'eligible','day':d.isoformat(),'kickoff':d.isoformat()+'T15:00:00+09:00','home_id':'A','away_id':'B','competition':'KLEAGUE1','market':'full_time_1x2','selection_order':['home','draw','away'],'prices':['2.20','3.10','3.40'],'price_source_sha256':'a'*64,'price_time_kind':'record_date_only','price_time':d.isoformat(),'identity_evidence':'audit:match','dc_p':[.4,.3,.3],'dc_reference':d.replace(day=1).isoformat(),'dc_training_cutoff':(d.replace(day=1)-timedelta(days=7)).isoformat(),'dc_model':'kleague_dc_half180_l2_10_monthly_lag7_rho_fixed','dc_training_sha256':'b'*64,'result_status':'official_final','home_score':2,'away_score':1,'official_result_evidence':'official:match'}

def test_synthetic_independent_replay(bank):
    rows=[target(0),target(1),{'id':'excluded','eligibility':'excluded','reason':'ambiguous'}]
    result,ledger=m.evaluate(rows,bank)
    assert result['opportunities']==3 and result['eligible']==2 and result['excluded']=={'ambiguous':1}
    assert result['models']['neighbor']['coverage_eligible']==1 and result['models']['neighbor']['coverage_opportunities']==2/3
    assert result['pairs']['neighbor_vs_market']['weeks']==2 and len(result['pairs']['neighbor_vs_market']['accuracy_delta_ci95'])==2
    for r,line in zip(rows,ledger):
        p=line['forecasts']['market']['p'];x=p+r['dc_p']
        near=sorted(bank.rows,key=lambda z:(math.sqrt(sum((a-b)**2 for a,b in zip(z[2]+z[3],x))),z[0]))[:60]
        for name,bank_rows,den in [('global',bank.rows,342),('neighbor',near,90)]:
            un=[max(1e-6,p[j]+sum(int(z[4]==j)-z[2][j] for z in bank_rows)/den) for j in range(3)]
            assert line['forecasts'][name]['p']==pytest.approx([v/sum(un) for v in un],abs=1e-12)
        for f in line['forecasts'].values():
            k=max(range(3),key=lambda j:f['p'][j]);expected=Decimal(r['prices'][k])-1 if k==line['result'] else Decimal(-1)
            assert Decimal(f['net'])==expected

def test_forecast_label_poison_and_gate(bank):
    a=target();b=copy.deepcopy(a);b['home_score']=0;b['away_score']=5
    _,one=m.evaluate([a],bank);_,two=m.evaluate([b],bank)
    assert {n:f['p'] for n,f in one[0]['forecasts'].items()}=={n:f['p'] for n,f in two[0]['forecasts'].items()}
    with pytest.raises(ValueError):m.forecast(bank,{'market_p':[.4,.3,.3],'dc_p':[.4,.3,.3],'y':0})

def test_empty_and_single_week(bank):
    r,l=m.evaluate([],bank);assert not l and r['eligible']==0 and r['models']['market']['roi'] is None
    assert m.evaluate([],bank)==(r,l)
    r,_=m.evaluate([target()],bank);assert r['pairs']['neighbor_vs_market']['accuracy_delta_ci95'] is None

@pytest.mark.parametrize('key,value',[('market','handicap'),('competition','OTHER'),('selection_order',['away','draw','home']),('prices',['2','3','NaN']),('result_status','live'),('home_score',-1),('day','2024-01-01'),('dc_training_cutoff','2026-03-01'),('dc_model','unknown'),('price_time_kind','unknown'),('identity_evidence','')])
def test_invalid_records_rejected(bank,key,value):
    r=target();r[key]=value
    with pytest.raises(ValueError):m.evaluate([r],bank)

def test_duplicate_bank_overlap_and_hash(bank,tmp_path):
    r=target()
    with pytest.raises(ValueError):m.evaluate([r,copy.deepcopy(r)],bank)
    r['id']=bank.rows[0][0]
    with pytest.raises(ValueError):m.evaluate([r],bank)
    p=tmp_path/'wrong';p.write_text('wrong')
    with pytest.raises(ValueError):m.load_bank(p)

def test_late_price_and_excluded_schema(bank):
    r=target();r['price_time_kind']='observed_at';r['price_time']=r['kickoff']
    with pytest.raises(ValueError):m.evaluate([r],bank)
    with pytest.raises(ValueError):m.evaluate([{'id':'x','eligibility':'excluded','reason':''}],bank)

def test_target_extra_label_and_old_period_rejected(bank):
    r=target();r['y']=1
    with pytest.raises(ValueError):m.evaluate([r],bank)
    r=target();r['day']='2025-03-10';r['kickoff']='2025-03-10T15:00:00+09:00';r['dc_reference']='2025-03-01';r['dc_training_cutoff']='2025-02-22';r['price_time']='2025-03-10'
    with pytest.raises(ValueError):m.evaluate([r],bank)

def test_exclusion_and_review_gate(bank):
    r,_=m.evaluate([{'id':'missing','eligibility':'excluded','reason':'unavailable DC'}],bank)
    assert r['eligible']==0 and r['models']['market']['coverage_opportunities']==0 and r['review_gate']['status']=='insufficient_data'
