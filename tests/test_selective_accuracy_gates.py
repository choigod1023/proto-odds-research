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
    from source_gate import gate, load_frames
    rows,_,_=gate(rows,load_frames())
    a=m.run(rows); cutoff=a['split'][2]['from']
    flipped=[dict(r,home_score=r['away_score'],away_score=r['home_score']) if r['match_date']>=cutoff else r for r in rows]
    b=m.run(flipped); assert (a['selected'],a['prediction_fingerprint'])==(b['selected'],b['prediction_fingerprint'])


def test_known_postkickoff_publication_excluded_unknown_retained():
    from source_gate import gate
    row={'fixture_id':'MLB:1','source_url':'https://example.test/a','author':'A','side':'home','kickoff':'2026-06-01T10:00:00+09:00'}
    raw={'source_url':row['source_url'],'author':'A','side':'home','source_reported_published_at':'2026-06-01T01:01:00Z'}
    frame={'event_id':'MLB:1','kickoff':row['kickoff'],'raw_forecasts':[raw]}
    kept,audit,counts=gate([row],[frame]); assert not kept and audit[0]['status']=='postkickoff' and counts['postkickoff']==1
    raw['source_reported_published_at']=None
    kept,audit,counts=gate([row],[frame]); assert kept==[row] and audit[0]['status']=='unknown'
    raw['source_reported_published_at']='2026-06-01T00:59:00Z'
    kept,audit,counts=gate([row],[frame]); assert kept==[row] and audit[0]['status']=='pre_kickoff_claim'

def test_real_metadata_gate_and_prediction_output_replay():
    import hashlib
    from source_gate import gate, load_frames
    rows=[json.loads(x) for x in (P/'input.jsonl').read_text(encoding='utf8').splitlines()]
    kept,audit,counts=gate(rows,load_frames()); assert counts=={'pre_kickoff_claim':137,'unknown':401,'unmatched':181,'postkickoff':4}
    assert len(kept)==719 and all(x['status']!='postkickoff' for x in gate(kept,load_frames())[1])
    result=m.run(kept); saved=(P/'predictions.jsonl').read_bytes(); assert saved==m.jsonl_bytes(result['predictions'])
    manifest=json.loads((P/'results.json').read_text(encoding='utf8')); assert hashlib.sha256(saved).hexdigest()==manifest['hashes']['predictions.jsonl']
    cutoff=result['split'][2]['from']; flipped=[dict(r,home_score=r['away_score'],away_score=r['home_score']) if r['match_date']>=cutoff else r for r in kept]
    assert hashlib.sha256(m.jsonl_bytes(m.run(flipped)['predictions'])).hexdigest()==manifest['hashes']['predictions.jsonl']
    assert len(result['predictions'])==489
    allow={'id','date','league','market','fixed','p','gap','agree','voters','odds','fixed_odds'}
    assert all(set(x['features'])==allow for x in result['predictions'])
    assert all(c['weekly_ci']['status'] in ('ok','degenerate_one_selected_week') for c in result['curves'] if c['gate']['n'])
    assert result['selected']=='league=KBO' and result['selected_weeks']==1
    assert result['ci_interpretation']=='unreliable: only one selected week'
    assert result['valid_for_inference'] is False and manifest['valid_for_inference'] is False
    assert result['inference_invalid_reason']==manifest['inference_invalid_reason']
    assert next(c for c in result['curves'] if c['candidate']=='voters>=2')['weekly_ci']['status']=='no selected events'
