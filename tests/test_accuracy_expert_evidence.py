import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'experiments/expert-evidence-20261031'
spec = importlib.util.spec_from_file_location('expert_evidence', ROOT / 'prepare.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def fixture():
    c={'cohort':module.COHORT,'league':'KBO','event_id':'id','home':'H','away':'A','kickoff':'2026-01-01T10:00:00+09:00','decision_at':'2026-01-01T00:30:00+00:00','sides':['home','away'],'odds':[2.,2.], 'market_p':[.5,.5], 'outcome':0, 'void':False, 'issue':[2026,1], 'game_no':'001','product_id':'product'}
    t={**c,'actual_votes':True,'home':'H1','away':'A1','identity_bridge':{'official_fixture_id':'id','source_names':['H','A'],'canonical_names':['H1','A1']},'price_evidence':'archive'}
    choices=[{'side':side,'odds':2.,'scores':{'market':.5},'line':None,'exact_offer_coordinates_saved':True,'offer_id':'offer','issue_year':2026,'round':1,'game_no':'001','market':'full_game_moneyline'} for side in c['sides']]
    author={'source':'site','author':'author','side':'home','source_reported_published_at':'2026-01-01T00:00:00+00:00','observed_at':'2026-01-02T00:00:00+00:00','document_sha256':'a'*64}
    f={'league':'KBO','event_id':'id','home':'H','away':'A','kickoff':c['kickoff'],'choices':choices,'raw_forecasts':[author]}
    return t,c,f

def test_exact_identity_product_and_no_outcome_leak():
    t,c,f=fixture(); features,results,rejects=module.build([t],[f],[c]); assert len(features)==1 and not rejects
    assert 'outcome' not in json.dumps(features) and results[0]['outcome_reconciliation']=='agree'
    c['outcome']=1
    poisoned,results,_=module.build([t],[f],[c]); assert poisoned==features and results[0]['outcome_reconciliation']=='disagree'
    for field,value in [('game_no','002'),('odds',[1.9,2.]),('home','other')]:
        altered=copy.deepcopy(c); altered[field]=value
        assert not module.build([t],[f],[altered])[0]

def test_timing_and_duplicate_author_not_qualified():
    t,c,f=fixture(); rows,_,_=module.build([t],[f],[c]); a=rows[0]['authors'][0]
    assert a['claim_by_decision'] and not a['observed_by_decision'] and a['availability_tier']=='retrospective'
    assert not a['external_receipt_verified'] and not rows[0]['quote_observed_by_decision_verified']
    f['raw_forecasts'].append(copy.deepcopy(f['raw_forecasts'][0]))
    rows,_,rejected=module.build([t],[f],[c]); assert not rows and rejected['ineligible_author_rows']==2
    t['decision_at']=c['decision_at']='2026-01-01T00:31:00+00:00'
    assert module.build([t],[f],[c])[2]['invalid_decision_or_t30']==1

def test_publication_after_decision_and_same_product_only():
    t,c,f=fixture(); f['raw_forecasts'][0]['source_reported_published_at']='2026-01-01T00:31:00+00:00'
    unrelated=copy.deepcopy(f['choices']);
    for row in unrelated: row['round']=2; row['offer_id']='different'
    f['choices']+=unrelated
    rows,_,rejected=module.build([t],[f],[c]); assert not rows and rejected['ineligible_author_rows']==1
    f['raw_forecasts'][0]['source_reported_published_at']='2026-01-01T00:00:00+00:00'
    assert len(module.build([t],[f],[c])[0])==1
    f['choices'].append(copy.deepcopy(f['choices'][0])); assert not module.build([t],[f],[c])[0]

def test_saved_inventory_split_and_counts():
    rows=[json.loads(x) for x in (ROOT/'features.jsonl').read_text(encoding='utf8').splitlines()]
    results=[json.loads(x) for x in (ROOT/'results.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows)==len(results)==446
    assert sum(x['overlaps_pr433_445'] for x in rows)==445
    assert all('outcome' not in x and not x['quote_observed_by_decision_verified'] for x in rows)
    assert all(not a['observed_by_decision'] and not a['external_receipt_verified'] for x in rows for a in x['authors'])
    assert all(x['outcome_reconciliation']=='agree' for x in results)
