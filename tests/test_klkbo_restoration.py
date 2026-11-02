import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / 'experiments' / 'klkbo-restoration-20261102'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    raw = json.dumps({'game':[{'G_DT':'20261101','HOME_ID':'SS','AWAY_ID':'HT',
        'G_TM':'14:00','SEASON_ID':2026,'G_ID':'fixture','B_PIT_P_ID':11,'T_PIT_P_ID':22,
        'B_PIT_P_NM':'home','T_PIT_P_NM':'away'}]}).encode()
    request = {'url':'https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList', 'status':200,
               'raw_base64':base64.b64encode(raw).decode(), 'raw_sha256':hashlib.sha256(raw).hexdigest(),
               'received_at':'2026-10-31T01:00:00Z'}
    facts = [{'official_game_id':'fixture','after_value':{'side':s,'team_id':t,'player_id':p,'player_name':s,
               'announcement_status':'probable'},'official':{'confirmed':False,'raw_sha256':request['raw_sha256'],
               'received_at':request['received_at']}}
             for s,t,p in [('home','SS','11'),('away','HT','22')]]
    record = {'event_id':'evt','kickoff':'2026-11-01T05:00:00Z', 'captured_at':'2026-10-31T02:00:00Z',
              'market_observed_at':'2026-10-31T01:50:00Z','ledger_sequence':1,'snapshot_id':'revision',
              'predictions':{'action':'withhold'},'features':{
               'fixture':{'year':2026,'league':'KBO','sport':'bs','home':'삼성','away':'KIA','date':'11.01(일) 14:00'},
               'options':[{'market':'승패','label':'','selection':s,'odds':o} for s,o in [('홈',1.7),('원정',2.1)]]}}
    snapshot={'prediction_tail':[{'record_type':'prediction','record':record}], 'fixture_prices':[],
              'documents':[{'name':'kbo_candidate_calendar_cache','payload':{'calendar_cache':{}}}]}
    return snapshot,{'requests':[request],'facts':facts}


def test_forward_links_unconfirmed_and_preserves_missing_evidence():
    s,c=fixture(); out=load('forward_link').link(s,c)
    assert out['official_game_id']=='fixture' and out['canonical_game_id'] is None
    assert out['latest_revision_in_partial_tail']['prediction']['action']=='withhold'
    assert not out['latest_revision_in_partial_tail']['frozen_status_verified']
    assert not out['official_probable']['final_starting_lineup_confirmed']
    assert out['exact_revision_settlement_count_in_partial_tail']==0


@pytest.mark.parametrize('mutation', ['bad_bytes','wrong_side','too_late','ambiguous_options'])
def test_forward_rejects_unsafe_joins(mutation):
    s,c=fixture()
    if mutation=='bad_bytes': c['requests'][0]['raw_sha256']='0'*64
    elif mutation=='wrong_side': c['facts'][0]['after_value']['team_id']='HT'
    elif mutation=='too_late': c['requests'][0]['received_at']='2026-11-01T04:31:00Z'
    else: s['prediction_tail'][0]['record']['features']['options'].append(copy.deepcopy(s['prediction_tail'][0]['record']['features']['options'][0]))
    with pytest.raises(ValueError): load('forward_link').link(s,c)


def test_kbo_viewstate_is_scalar_only_and_separates_game_and_player():
    xml='<Table1 x="1"><G_ID>game</G_ID><G_DT>2026-03-29</G_DT><TEAM_ID>LG</TEAM_ID><TB_SC>B</TB_SC><RUN_CN>5</RUN_CN></Table1><Table1 x="1"><G_ID>game</G_ID><TB_SC>B</TB_SC><P_ID>123</P_ID><SEQ_NO>1</SEQ_NO></Table1>'
    encoded=base64.b64encode(xml.encode()).decode()
    games,players=load('reconcile').extract_kbo(f'<input id="__VIEWSTATE" value="{encoded}">')
    assert games['game']['B'][0]['TEAM_ID']=='LG'
    assert players['game'][0]['official_player_id']=='123'
    assert 'game' not in games or '123' not in str(games['game'])


def test_duplicate_physical_game_does_not_double_count_or_hide_conflicting_odds():
    rows=[{'fixture_id':'same','target_event_id':'primary','market':{'odds':[1.7,2.0]},'limitations':[]},
          {'fixture_id':'same','target_event_id':'aux','market':{'odds':[1.8,1.9]},'limitations':[]}]
    merged=load('reconcile').dedupe_kbo(rows)
    assert len(merged)==1 and merged[0]['source_case_rows']==2
    assert merged[0]['market'] is None
    assert 'conflicting_duplicate_prices' in merged[0]['limitations']
