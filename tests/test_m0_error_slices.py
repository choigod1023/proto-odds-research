import copy
import gzip
import json
from pathlib import Path

import pytest
from scripts.audit_m0_error_slices import analyze, index_gzip, metric, group_unknown


def sample():
    models={}
    for name,pick,net in [('m0_actual','home','0.5'),('m0_projected','away','-1'),('m1_projected','away','-1')]:
        models[name]={'pick':pick,'net':net,'home_probability':0.6 if pick=='home' else 0.4}
    row={'fixture_id':'f','date':'2026-03-28','winner':'home','prices':{'home':1.5,'away':2.5},'in_selected_103':False,'models':models}
    features={}
    raw={}
    roster={}
    for side in ('home','away'):
        roster[side]={'hitters':['known']*9,'starter':'known'}
        for name in ('lineup_strength','starter_strength','bullpen_load','rest_days','team_strength'):
            features[side+'_'+name]={'value': 150 if name=='rest_days' else None if name=='starter_strength' else 1}
        raw[side+'_team_strength']={'value':[{'date':'2025-10-01'}]}
        raw[side+'_lineup_strength']={'value':[{'history':None}]}
    features['park_factor']={'value':None}
    f={'event_date':'2026-03-28','roster':roster,'features':features,'raw_features':raw}
    label={'home_score':2,'away_score':1,'status':'final','settlement_rule':None}
    return row,f,label


def full():
    row,f,label=sample(); rows=[]; fs={}; ls={}
    for i in range(667):
        fid=str(i); r=copy.deepcopy(row); r['fixture_id']=fid; rows.append(r); fs[fid]=copy.deepcopy(f); ls[fid]=copy.deepcopy(label)
    stats=metric(rows)
    replay={'rows':rows,'metrics':{'all667':{m:{'hits':stats[m]['hits'],'decided':667,'net_units':stats[m]['net_units'],'decisive_log_loss':stats[m]['log_loss']} for m in ('m0_actual','m0_projected','m1_projected')}}}
    replay['metrics']['selected103']={m:{'bets':0,'hits':0,'net_units':'0'} for m in ('m0_actual','m0_projected','m1_projected')}
    replay['metrics']['remaining564']={m:{'bets':667,'hits':stats[m]['hits'],'net_units':stats[m]['net_units']} for m in ('m0_actual','m0_projected','m1_projected')}
    return replay,fs,ls,{'spec':{'hitters':['known'],'starters':['known']}}


def test_paired_errors_void_and_unknown():
    r,_,_=sample(); void=copy.deepcopy(r); void['winner']='draw'
    for m in void['models'].values(): m['net']='0'
    result=metric([r,void]); assert result['refunds']==1 and result['decided']==1
    assert result['m0_actual_vs_m1']=={'correct/correct':0,'correct/error':1,'error/correct':0,'error/error':0}
    assert result['m0_actual']['net_units']=='0.5'
    assert group_unknown([0,False,1,False])=='unknown_present'
    assert group_unknown([None,False,0,False])=='roster_missing'


def test_full_partition_and_missing():
    replay,fs,ls,model=full(); out=analyze(replay,fs,ls,model)
    assert out['missing_features']['label_settlement_rule']['missing']==667
    assert out['slices']['rest']['100_plus_any']['n']==667
    assert out['slices']['starter_strength_missing']['both_sides']['n']==667
    assert out['total']['m0_actual_vs_m1']['correct/error']==667
    for cells in out['slices'].values(): assert sum(x['n'] for x in cells.values())==667


def test_rejects_conflicting_outcome_and_wrong_aggregate():
    replay,fs,ls,model=full(); ls['1']['away_score']=3
    with pytest.raises(ValueError,match='label mismatch'): analyze(replay,fs,ls,model)
    ls['1']['away_score']=1; replay['metrics']['all667']['m0_actual']['hits']-=1
    with pytest.raises(ValueError,match='replay total mismatch'): analyze(replay,fs,ls,model)


def test_duplicate_inputs_rejected(tmp_path):
    p=tmp_path/'x.gz'
    with gzip.open(p,'wt',encoding='utf8') as stream: stream.write(json.dumps({'fixture_id':'same'})+'\n'+json.dumps({'fixture_id':'same'})+'\n')
    with pytest.raises(ValueError,match='duplicate'): index_gzip(p)
