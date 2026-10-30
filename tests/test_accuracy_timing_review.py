import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

P=Path(__file__).resolve().parents[1]/'experiments/accuracy-timing-review-20261030/vendor'

def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

prepare=load('parent_prepare',P/'parent/prepare.py')
structure=load('review_structure',P/'structure/validator.py')
match=load('review_match',P/'connection/match.py')

def frame():
    return {'event_id':'game-1','league':'KBO','home':'H','away':'A','kickoff':'2026-10-30T10:00:00+00:00','raw_forecasts':[{'source_reported_published_at':'2026-10-30T09:20:00Z'}]}

@pytest.mark.parametrize('change,decision,kickoff,expected',[
    (lambda f:None,'2026-10-30T18:30:00+09:00','2026-10-30T19:00:00+09:00',None),
    (lambda f:None,None,'2026-10-30T10:00:00Z','unknown_or_naive_publication'),
    (lambda f:None,'2026-10-30T09:30:00Z',None,'unknown_or_naive_publication'),
    (lambda f:f.update(kickoff='2026-10-30T10:00:00'),'2026-10-30T09:30:00Z','2026-10-30T10:00:00Z','unknown_or_naive_publication'),
    (lambda f:f['raw_forecasts'].append({}),'2026-10-30T09:30:00Z','2026-10-30T10:00:00Z','unknown_or_naive_publication'),
    (lambda f:None,'2026-10-30T10:00:00Z','2026-10-30T10:00:00Z','invalid_decision_time'),
    (lambda f:f.update(kickoff='2026-10-30T10:01:00Z'),'2026-10-30T09:30:00Z','2026-10-30T10:00:00Z','frame_kickoff_mismatch'),
    (lambda f:f['raw_forecasts'][0].update(source_reported_published_at='2026-10-30T09:30:00.000001Z'),'2026-10-30T09:30:00Z','2026-10-30T10:00:00Z','postdecision_publication'),
])
def test_timing_boundary_fails_closed(change,decision,kickoff,expected):
    f=frame();change(f);assert prepare.publication_rejection(f,decision,kickoff)==expected

def test_predating_or_reidentifying_decision_not_established_by_guard():
    f=frame();f['raw_forecasts'][0]['source_reported_published_at']='2026-10-30T09:40:00Z'
    assert prepare.publication_rejection(f,'2026-10-30T09:30:00Z',f['kickoff'])=='postdecision_publication'
    assert prepare.publication_rejection(f,'2026-10-30T09:45:00Z',f['kickoff']) is None
    f.update(event_id='different-event',league='MLB',home='X',away='Y')
    assert prepare.publication_rejection(f,'2026-10-30T09:45:00Z',f['kickoff']) is None
    # Caller indexes event_id but does not check frame league or team against the case.

def test_v11_reference_hash_rejects_quote_change_but_not_game_identity_change():
    d=json.loads((P/'structure/fixture-pregame.json').read_text(encoding='utf8'))
    assert structure.validate(d)
    changed=copy.deepcopy(d);changed['quotes'][0]['odds']['home']=9.9
    with pytest.raises(structure.Invalid,match='input content hash mismatch'):structure.validate(changed)
    changed=copy.deepcopy(d);changed['games'][0]['away_team_id']='unverified-other-team'
    assert structure.validate(changed)
    assert changed['freezes'][0]['freeze_hash']==d['freezes'][0]['freeze_hash']
    with pytest.raises(structure.Invalid,match='history changed'):structure.validate_append(d,changed)

def test_v11_duplicate_official_id_under_distinct_keys_is_not_rejected():
    prior=[{'league':'K리그2','date':f'2025-01-0{i}','home':f'H{i}','away':f'A{i}','hg':1,'ag':0} for i in (1,2)]
    official=[{'league':r['league'],'kickoff_at':r['date']+'T15:00:00+09:00','home':r['home'],'away':r['away'],'official_game_id':'same-id','score':[1,0]} for r in prior]
    output=match.link(prior,official)
    assert len(output['links'])==2 and len({x['official_game_id'] for x in output['links']})==1
    official.append(copy.deepcopy(official[0]))
    output=match.link(prior,official)
    assert len(output['links'])==1 and output['unmatched'][0]['reason']=='ambiguous'
