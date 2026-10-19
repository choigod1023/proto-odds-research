import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from recommendation_history import attach_official_scores, settle_history
from runtime_datasets import RESULT_SCHEMA, RESULT_INSERT, result_row
from runtime_db import RuntimeDatabase


def test_regular_artifact_publish_enriches_archived_result(tmp_path):
    db = RuntimeDatabase(tmp_path / 'scores.sqlite3')
    now = datetime.now(timezone.utc)
    e = dict(kickoff_at=now.isoformat(), round=123, sport='bs', league='KBO',
             home='KIA', away='LG', result='hit', result_source='official', sel='홈', odds=1.57)
    db.import_artifact('today_combo', {'generated_at':now.isoformat(), 'recommendation_history':{'a':e}})
    from recommendation_history import KST
    with db.transaction() as c:
        c.execute(RESULT_INSERT, ('proto', now.year, 123, '8804',
                  now.astimezone(KST).replace(tzinfo=None).isoformat(), 'KBO', 'bs', 'KIA', 'LG',
                  5, 1, now.isoformat(), 'confirmed'))
    db.store_artifact('today_combo', {'generated_at':now.isoformat(), 'candidates':[]})
    saved=db.get_artifact('today_combo')['recommendation_history']['a']
    assert saved['official_score']['score']==[5,1]
    assert saved['sel']=='홈' and saved['odds']==1.57


def test_indexed_score_join_preserves_pick_and_handles_correction_and_invalidation():
    c = sqlite3.connect(':memory:')
    c.executescript(RESULT_SCHEMA)
    row = dict(year=2026, round=123, game_no='8804', date_text='10.17(토) 14:00',
               sport='bs', league='KBO', home='KIA 5', away='1 LG', market_family='승패', result='홈승')
    def insert(r, at):
        c.execute(RESULT_INSERT, result_row(r, 'proto', at))
    insert(row, '2026-10-17T10:00:00Z')
    insert({**row, 'game_no':'9119', 'date_text':'10.18(일) 14:00', 'home':'KIA 6', 'away':'3 LG'}, '2026-10-18T10:00:00Z')
    e = dict(kickoff_at='2026-10-17T14:00:00+09:00', round=123, game_no='8805',
             sport='bs',league='KBO',home='KIA',away='LG',market='핸디캡',sel='핸디홈',market_label='H -1.5',
             result='hit',result_source='official',odds=1.6,probability=.6)
    h={'one':e}
    attach_official_scores(h,c)
    assert e['official_score']['score']==[5,1]
    assert e['sel']=='핸디홈' and e['odds']==1.6 and e['probability']==.6
    insert({**row,'home':'KIA 4'}, '2026-10-19T10:00:00Z')
    attach_official_scores(h,c)
    assert e['official_score']['score']==[4,1]
    insert({**row,'result':'취소'}, '2026-10-20T10:00:00Z')
    attach_official_scores(h,c)
    assert 'official_score' not in e
    c.close()


def test_ambiguous_wrong_sport_and_void_scores_are_not_attached():
    c=sqlite3.connect(':memory:'); c.executescript(RESULT_SCHEMA)
    row=dict(year=2026,round=123,game_no='1',date_text='10.17(토) 14:00',sport='bs',league='KBO',
             home='KIA 5',away='1 LG',market_family='승패',result='홈승')
    for r in [row,{**row,'game_no':'2','home':'KIA 4'}]:
        c.execute(RESULT_INSERT,result_row(r,'proto','2026-10-18T10:00:00Z'))
    e=dict(kickoff_at='2026-10-17T14:00:00+09:00',round=123,sport='bs',league='KBO',home='KIA',away='LG',result='hit',result_source='official')
    assert 'official_score' not in attach_official_scores({'a':e},c)['a']
    c.execute("DELETE FROM match_results WHERE game_no='2'")
    for changes in ({'sport':'sc'}, {'result':'void'}, {'result_source':'live'}):
        other={**e, **changes}
        assert 'official_score' not in attach_official_scores({'a':other},c)['a']
    c.close()


def test_exact_official_market_outcome_is_retained():
    e=dict(kickoff_at='2026-10-17T14:00:00+09:00',round=123,game_no='1',home='KIA',away='LG',date='10.17(토) 14:00',market='언더오버',market_label='U 7.5',sel='오버')
    r={**e,'label':'U 7.5','n_way':2,'result':'언더'}
    settle_history({'a':e},{'markets':{'123':{'1':r}}},datetime(2026,10,18,tzinfo=timezone.utc))
    assert e['result']=='miss' and e['official_market_result']=='언더'
