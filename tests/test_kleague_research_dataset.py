import json

import pandas as pd
import pytest

from scripts.build_kleague_research_dataset import build,team


@pytest.mark.parametrize('conflict',[False,True])
def test_reissues_are_deduplicated_but_score_conflicts_are_rejected(tmp_path,conflict):
    csv=tmp_path/'games.csv';detail=tmp_path/'detail.json';official=tmp_path/'official.json';cohort=tmp_path/'cohort.json'
    record=dict(league='K리그1',market_family='승무패',is_void=False,result='홈승',
        home='FC서울 1',away='0 전북현대',date_text='03.01(금) 19:00',year=2024,round=20)
    pd.DataFrame([record,record]).to_csv(csv,index=False)
    official.write_text(json.dumps([dict(league='K리그1',match_date='2025-03-01',home='서울',away='전북',
        home_score=1,away_score=0,source_url='https://official.example/result')]),encoding='utf8')
    detail.write_text(json.dumps({'one':dict(home='서울',away='전북',date='2025-03-01',
        home_score=2 if conflict else 1,away_score=0,data={}),
        'playoff':dict(home='서울',away='K2-team',date='2025-12-01',home_score=1,away_score=0,data={})}),encoding='utf8')
    cohort.write_text(json.dumps(dict(event_ledger=[dict(league='K리그1',home='서울',away='전북',
        match_date='2025-03-01',winner='home',picks={'market':'home'})])),encoding='utf8')
    history,targets,excluded=build(csv,detail,official,cohort)
    assert sum(r['date']=='2024-03-01' for r in history)==1
    assert len(targets)==int(not conflict)
    assert excluded['detail_outside_k1_members']==1
    if conflict:assert excluded['score_conflict']==1


def test_explicit_team_aliases_keep_the_same_physical_fixture():
    assert team('FC서울')=='서울' and team('서울이랜')=='서울E'
    assert team('제주SKFC')=='제주' and team('수원FC')=='수원FC'
