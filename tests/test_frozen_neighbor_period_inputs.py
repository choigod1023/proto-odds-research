from copy import deepcopy
from datetime import date, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

BASE=Path(__file__).resolve().parents[1]/'experiments/frozen-neighbor-period-20261102'
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location('fixed_period_prepare',BASE/'prepare.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_official_response_bytes_rebuild_exact_normalized_fixtures():
    history,official=m.source_history()
    assert len(history)==888 and len(official)==204
    assert all(x['source_status']=='FE' and x['source_end_yn']=='Y' for x in official)
    assert len({r['id'] for r in history})==888


def test_corrupted_raw_bytes_are_rejected(tmp_path):
    receipt=json.loads((BASE/'official-2026/02.receipt.json').read_text(encoding='utf8'))
    (tmp_path/receipt['raw_file']).write_bytes(b'{}')
    with pytest.raises(ValueError,match='raw byte mismatch'):
        m.parse_entries([receipt],tmp_path)


@pytest.mark.parametrize('field,value',[('year','2025'),('league','K리그2'),('n_way','2'),('market_label','핸디캡')])
def test_wrong_year_league_or_market_not_accepted(field,value):
    row=m.read_lines(BASE/'inputs/domestic-2026.raw.jsonl')[0]
    assert m.parse_domestic(row)
    row[field]=value
    assert m.parse_domestic(row) is None


def test_duplicate_prices_and_bad_clock_final_score_are_not_silently_selected():
    _,official=m.source_history();row=m.parse_domestic(m.read_lines(BASE/'inputs/domestic-2026.raw.jsonl')[0])
    good,bad=m.link([row,deepcopy(row)],official)
    assert not good and [r['reason'] for r in bad]==['duplicate_price_fixture']*2
    bad_clock=deepcopy(row);bad_clock['kickoff']=row['day']+'T01:00:00'
    bad_score=deepcopy(row);bad_score['recorded_scores']=[99,99]
    good,bad=m.link([bad_clock,bad_score],official)
    assert not good and {r['reason'] for r in bad}=={'local_clock_conflict','archived_score_conflict'}


def test_saved_dc_states_exclude_future_and_keep_original_source_fields():
    history,_=m.source_history();byid={r['id']:r for r in history}
    states=json.loads((BASE/'dc-states.json').read_text(encoding='utf8'))
    for month,entry in states.items():
        cutoff=(date.fromisoformat(month+'-01')-timedelta(days=7)).isoformat()
        rows=entry['training_rows']
        assert all(r['day']<=cutoff for r in rows)
        assert {r['id'] for r in rows}=={r['id'] for r in history if r['day']<=cutoff}
        assert rows==[byid[r['id']] for r in rows]
        assert entry['training_sha256']==hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        assert entry['state']['cutoff_inclusive']==cutoff


def test_future_result_mutation_cannot_change_earlier_month_state():
    history,_=m.source_history();changed=deepcopy(history)
    for r in changed:
        if r['day']>'2026-01-25':r['hg'],r['ag']=99,98
    original=m.monthly_states(history,['2026-02'])
    poisoned=m.monthly_states(changed,['2026-02'])
    assert original==poisoned


def test_two_price_sources_are_not_counted_as_independent_games():
    d=json.loads((BASE/'domestic-opportunities.json').read_text(encoding='utf8'))
    o=json.loads((BASE/'overseas-opportunities.json').read_text(encoding='utf8'))
    a={r['id'] for r in d if r['eligibility']=='eligible'};b={r['id'] for r in o if r['eligibility']=='eligible'}
    assert len(d)==89 and len(a)==77 and len(o)==len(b)==120
    assert len(a&b)==77 and len(a|b)==120


def test_unlabelled_overseas_prices_cannot_enter_verified_evaluation():
    rows=json.loads((BASE/'verified-inputs/overseas-opportunities.json').read_text(encoding='utf8'))
    assert len(rows)==120 and all(r['eligibility']=='excluded' and r['reason']=='unverified_market_and_selection_order' for r in rows)


def test_every_valid_forecast_and_settlement_has_independent_scalar_replay():
    s=importlib.util.spec_from_file_location('verify_period_replay',BASE/'verify.py')
    v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
    report=v.verify()
    assert report['verified_games']==77 and report['verified_decimal_settlements']==231
