import importlib.util,json,hashlib,pathlib
import pytest
HERE=pathlib.Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('audit_projection',HERE/'analyze_projection_accuracy.py'); a=importlib.util.module_from_spec(sp);sp.loader.exec_module(a)


def mock_row():
    f={}
    for side in ('home','away'):
        f[side+'_actual_starter_assumed']={'value':'P'+side}
        f[side+'_actual_lineup_assumed']={'value':[side+str(n) for n in range(9)]}
    r={'fixture_id':'g','season':2025,'features':f}
    proj={'fixture_id':'g','scenario':'projected_from_prior_games','projection':{},'features':{}}
    for side in ('home','away'):
        ids=[side+str(n) for n in range(9)]
        proj['projection'][side+'_starter']={'player_id':'P'+side}
        proj['projection'][side+'_lineup']={'player_ids':ids}
        proj['features'][side+'_lineup_strength']={'value':[{'player_id':x,'history':[{'ab':1,'hit':1,'bb':0,'hr':0}]} for x in ids]}
    return r,proj


def test_pairwise_denominators_exact_and_missing():
    row,pr=mock_row();pr['projection']['away_starter']['player_id']='wrong';pr['projection']['away_lineup']['player_ids'][-1]='extra'
    pr['features']['away_lineup_strength']['value'][-1]={'player_id':'extra','history':None}
    d=a.compute([row],[pr],{}, {})['2025']
    assert (d['starter_compared_sides'],d['starter_matched_sides'])==(2,1)
    assert (d['starter_both_sides_compared_games'],d['starter_both_sides_match_games'])==(1,0)
    assert (d['lineup_actual_player_slots'],d['lineup_overlap_player_slots'])==(18,17)
    assert (d['lineup_exact_set_sides'],d['lineup_exact_order_sides'])==(1,1)
    assert (d['projected_player_slots'],d['player_history_present'],d['player_history_missing'])==(18,17,1)
    row['features']['away_actual_starter_assumed']['value']=None;pr['projection']['away_starter']['player_id']=None
    d=a.compute([row],[pr],{}, {})['2025'];assert d['starter_both_missing']==1 and d['starter_both_sides_compared_games']==0


def test_conflict_and_missing_are_separate():
    row,pr=mock_row();row['features']['home_actual_starter_assumed']['value']=None
    box={'g':{'data':{'home':[{'pcode':'1','name':'one'}]}}}; starters={'g':[{'home_starter':'other'}]}
    assert a.starter_status(row,'home',box,starters)=='source_name_conflict'
    assert a.starter_status(row,'home',{},starters)=='source_missing_or_unusable'


def test_reject_ambiguous_inputs_and_verify_results_hashes():
    row,pr=mock_row()
    with pytest.raises(ValueError):a.compute([row,row],[pr],{}, {})
    pr['projection']['home_lineup']['player_ids'][0]='broken'
    with pytest.raises(ValueError):a.compute([row],[pr],{}, {})
    result=json.loads((HERE/'projection-accuracy.json').read_text(encoding='utf8'))
    for name,h in result['input_sha256'].items():assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==h
    assert result['phase_counts']=={'unknown':2913} and result['point_in_time_verified']==0
