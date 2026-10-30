import importlib.util, json
from pathlib import Path
import sys
P=Path(__file__).resolve().parents[1]/'experiments'/'selective-accuracy-gates-20261030'
sys.path.insert(0,str(P))
spec=importlib.util.spec_from_file_location('gate_research',P/'run.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def test_independent_settlement_and_payout():
    for winner in ('home','away'):
        for pick in ('home','away'):
            assert m.score({'winner':winner},pick,2.17)==m.alt({'winner':winner},pick,2.17)

def test_features_exclude_outcome_and_postgame_fields():
    e={'fixture_id':'a','match_date':'2026-01-01','league':'MLB','order':['home','away'],'prices':[1.5,2.5],'picks':{'market':'home','market90_vote10':'home'},'vote_counts':{'home':1},'voters':1,'winner':'home'}
    x=m.feature(e); e.update(winner='away', home_score=0, postgame='junk', current_final_odds=[99,99]); assert m.feature(e)==x
    assert not ({'winner','home_score','postgame','current_final_odds'} & set(x))

def test_holdout_label_flip_does_not_change_selection_or_predictions():
    rows=[json.loads(x) for x in (P/'input.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    a=m.run(rows); cutoff=a['split'][2]['from']
    flipped=[dict(r,home_score=r['away_score'],away_score=r['home_score']) if r['match_date']>=cutoff else r for r in rows]
    b=m.run(flipped); assert (a['selected'],a['prediction_fingerprint'])==(b['selected'],b['prediction_fingerprint'])
