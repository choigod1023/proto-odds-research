import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

BASE=Path('experiments/kleague-roi-diagnostic-20261101/training')
spec=importlib.util.spec_from_file_location('kleague_capture',BASE/'fetch.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def fixture(**changes):
    return {**{'year':2023,'leagueId':1,'meetSeq':1,'gameId':1,'gameDate':'2023.02.25','gameTime':'16:30','roundId':1,'homeTeam':'A','awayTeam':'B','homeTeamName':'팀A','awayTeamName':'팀B','homeGoal':1,'awayGoal':0,'gameStatus':'FE','endYn':'Y'},**changes}


def entry(tmp_path,rows):
    data=json.dumps({'data':{'scheduleList':rows}},ensure_ascii=False).encode('utf8');(tmp_path/'raw.json').write_bytes(data)
    return {'payload':{'year':'2023','month':'02'},'status':'ok','raw_file':'raw.json','sha256':hashlib.sha256(data).hexdigest(),'received_at':'2026-11-01T01:00:00+00:00','row_count':len(rows)}


def test_unique_scope_score_and_exclusions(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'BASE',tmp_path)
    rows=[fixture(),fixture(gameId=2,meetSeq=3),fixture(gameId=3,gameStatus='CANCEL',endYn='N'),fixture(gameId=4,homeGoal=None),fixture(gameId=5,year=2024)]
    accepted,excluded,conflicts,months=mod.process([entry(tmp_path,rows)])
    assert len(accepted)==1 and accepted[0]['scores']=={'home':1,'away':0}
    assert {x['reason'] for x in excluded}=={'different_competition','not_final_or_cancelled','invalid_score','wrong_year'}
    assert not conflicts and months[0]['accepted']==1


def test_duplicate_and_name_change_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'BASE',tmp_path)
    rows=[fixture(),fixture(),fixture(gameId=2,homeTeamName='다른이름')]
    accepted,excluded,conflicts,_=mod.process([entry(tmp_path,rows)])
    assert not accepted and set(x['reason'] for x in excluded)=={'duplicate_fixture_id','team_name_changed'}
    assert conflicts['A']==['다른이름','팀A']


def test_source_tampering_and_invalid_date(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'BASE',tmp_path)
    e=entry(tmp_path,[fixture(gameDate='2023.02.30')]);out=mod.process([e]);assert out[1][0]['reason']=='invalid_day'
    (tmp_path/'raw.json').write_bytes(b'{}')
    with pytest.raises(ValueError,match='source hash mismatch'):mod.process([e])


def test_existing_capture_does_not_repeat(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'BASE',tmp_path);(tmp_path/'requests.json').write_text('[]',encoding='utf8')
    with pytest.raises(RuntimeError,match='already exists'):mod.capture()


def test_real_evidence_receipts_and_counts():
    m=json.loads((BASE/'manifest.json').read_text(encoding='utf8')); es=json.loads((BASE/'requests.json').read_text(encoding='utf8'))
    assert len(es)==24 and all(e['status']=='ok' and e.get('received_at') for e in es)
    assert m['counts']['accepted_by_year']=={'2023':228,'2024':228}
    assert m['counts']['excluded_by_reason']=={'different_competition':8}
    for e in es: assert hashlib.sha256((BASE/e['raw_file']).read_bytes()).hexdigest()==e['sha256']
    rows=[json.loads(s) for s in (BASE/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows)==456 and len({x['fixture_id'] for x in rows})==456
    assert all(x['source_status']=='FE' and x['source_end_yn']=='Y' for x in rows)


def test_each_season_has_38_rounds_and_12_teams():
    from collections import Counter
    rows=[json.loads(s) for s in (BASE/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    for year in (2023,2024):
        season=[x for x in rows if x['year']==year]
        assert len(Counter(x['round_id'] for x in season))==38
        assert set(Counter(x['round_id'] for x in season).values())=={6}
        teams=Counter(t for x in season for t in (x['home_team_id'],x['away_team_id']))
        assert len(teams)==12 and set(teams.values())=={38}
