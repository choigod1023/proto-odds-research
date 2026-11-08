"""Compare committed v2 design to independently specified 12-column correction."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import numpy as np
import audit_v2_design as reference
from audit_frozen import WORKER,ROOT,git

CODE='849e43a5c9e7f8da6b32fd3e097ad5e44ee1d216';V1='2156ec6b2c7c54a104c051c5cddc5a9d0b9d46f7';SELECTION='d28b4101f81fea02d4046dea7d05c47449fffb55'

def main():
    reference.correction_contract()
    for path in ('research.py','original_adapter.py','original_model.py','contract.json','artifacts/selection.json','artifacts/final-models.json','artifacts/predictions.json','artifacts/results.json'):
        assert git(WORKER,CODE,ROOT/path)==git(WORKER,V1,ROOT/path),path
    raw=git(WORKER,SELECTION,ROOT/'v2/selection.json');assert hashlib.sha256(raw).hexdigest()=='9fa9c300ce4e74c938eefcbdcb745a06fed3006f79fc59855f33fe557e0ff611'
    selected=json.loads(raw);old=json.loads(git(WORKER,V1,ROOT/'artifacts/selection.json'))
    assert selected['correction_contract_sha256']==reference.SHA and selected['outcomes_after_2025_read'] is False and selected['retrospective_correction_after_v1_2026_seen'] is True
    for name in ('M0','M1'):assert selected['outer']['families'][name]['metrics']==old['outer']['models'][name]['metrics']
    spec={'teams':['H','A'],'hitters':['known'+str(i) for i in range(18)],'starters':['hp','ap'],'mean':[0.]*8,'scale':[1.]*8}
    vals={s+'_'+k:0. for s in ('home','away') for k in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load')};vals['park_factor']=0.
    row={'home_id':'H','away_id':'A','values':vals,'roster':{'home':{'hitters':['known'+str(i) for i in range(9)],'starter':'hp'},'away':{'hitters':['known'+str(i) for i in range(9,18)],'starter':'ap'}}}
    variants=[copy.deepcopy(row) for _ in range(5)]
    for side in ('home','away'):
        variants[1]['roster'][side]={'hitters':None,'starter':None}
        variants[2]['roster'][side]={'hitters':[side+str(i) for i in range(9)],'starter':side+'unknown'}
        variants[3]['values'][side+'_lineup_strength']=None
    variants[4]['roster']['home']['hitters'][0]='unknown';variants[4]['roster']['away']['starter']=None
    swapped=copy.deepcopy(variants[4]);swapped['home_id'],swapped['away_id']=swapped['away_id'],swapped['home_id'];swapped['roster']['home'],swapped['roster']['away']=swapped['roster']['away'],swapped['roster']['home']
    for key in ('team_strength','rest_days','lineup_strength','starter_strength','bullpen_load'):
        swapped['values']['home_'+key],swapped['values']['away_'+key]=swapped['values']['away_'+key],swapped['values']['home_'+key]
    variants.append(swapped)
    with tempfile.TemporaryDirectory(prefix='v2-code-audit-',dir=Path(__file__).resolve().parent) as tmp:
        root=Path(tmp);(root/'v2').mkdir()
        for path in ('research.py','original_adapter.py'):(root/path).write_bytes(git(WORKER,CODE,ROOT/path))
        (root/'v2/research_v2.py').write_bytes(git(WORKER,CODE,ROOT/'v2/research_v2.py'))
        specmod=importlib.util.spec_from_file_location('review_committed_v2',root/'v2/research_v2.py');module=importlib.util.module_from_spec(specmod);specmod.loader.exec_module(module)
        max_error=0.
        for x in variants:
            worker=copy.deepcopy(x);worker['features']={k:{'value':v} for k,v in worker.pop('values').items()}
            actual,nbase=module.design([worker],spec);expected,_,_=reference.design(x,spec);assert nbase==1+len(spec['teams'])+16+4+12
            max_error=max(max_error,float(np.max(np.abs(actual.toarray()[0]-expected))))
        assert max_error<1e-14
        v1_1=reference.v1.design_one(variants[1],spec,'M2')[0];v1_2=reference.v1.design_one(variants[2],spec,'M2')[0];v2_1=reference.design(variants[1],spec)[0];v2_2=reference.design(variants[2],spec)[0]
        assert np.array_equal(v1_1,v1_2) and not np.array_equal(v2_1,v2_2)
        v1_3=reference.v1.design_one(variants[3],spec,'M2')[0];v1_0=reference.v1.design_one(variants[0],spec,'M2')[0];v2_3=reference.design(variants[3],spec)[0];v2_0=reference.design(variants[0],spec)[0]
        assert np.array_equal(v1_3,v1_0) and not np.array_equal(v2_3,v2_0)
    print(json.dumps({'status':'PASS','worker_code_commit':CODE,'selection_commit':SELECTION,'unchanged_v1_files':8,'paired_roster_corrected':True,'paired_numeric_corrected':True,'added_columns':12,'max_design_difference':max_error,'scope':'committed_design_only'}))
if __name__=='__main__':main()
