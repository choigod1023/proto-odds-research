from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from kbo_candidate_source import day_manifest,collect


def final():
    return dict(LE_ID=1,G_DT='20261029',G_TM='18:30',G_ID='20261029SSHT0',
                HOME_NM='KIA',AWAY_NM='삼성',GAME_STATE_SC='3',GAME_RESULT_CK=1,
                CANCEL_SC_ID='0',B_SCORE_CN='4',T_SCORE_CN='6')


def test_empty_off_day_and_actual_official_final_shape():
    empty=day_manifest({'code':'100','game':[]},day='2026-10-28',observed_at='2026-10-29T14:00:00+00:00',source_sha256='a'*64)
    assert empty['manifest']['complete'] and not empty['results']
    item=day_manifest({'code':'100','game':[final()]},day='2026-10-29',observed_at='2026-10-29T14:00:00+00:00',source_sha256='b'*64)
    assert item['results'][0]['hg']==4 and item['results'][0]['ag']==6
    assert item['results'][0]['availability_basis']=='official_final_observed_at'


@pytest.mark.parametrize('defect',['wrong_date','wrong_team','pending','no_result','cancelled'])
def test_unfinished_unknown_or_cancelled_are_not_final_scores(defect):
    r=final()
    if defect=='wrong_date':r['G_DT']='20261028'
    if defect=='wrong_team':r['HOME_NM']='unknown'
    if defect=='pending':r['GAME_STATE_SC']='2'
    if defect=='no_result':r['GAME_RESULT_CK']=0
    if defect=='cancelled':r['CANCEL_SC_ID']='2'
    args=dict(day='2026-10-29',observed_at='2026-10-29T14:00:00+00:00',source_sha256='b'*64)
    if defect in {'wrong_date','wrong_team'}:
        with pytest.raises(ValueError):day_manifest({'code':'100','game':[r]},**args)
    else:
        item=day_manifest({'code':'100','game':[r]},**args)
        assert not item['results']
        assert item['manifest']['complete']==(defect=='cancelled')


class Response:
    def __init__(self,body):self.body=body
    def raise_for_status(self):pass
    def iter_content(self,**kw):yield json.dumps(self.body).encode()
    def close(self):pass


class Session:
    def __init__(self,empty=False,pending=False):self.calls=[];self.empty=empty;self.pending=pending
    def post(self,url,**kw):
        self.calls.append((url,kw))
        rows=[final()] if kw['data']['date']=='20261029' and not self.empty else []
        if rows and self.pending:rows[0]['GAME_STATE_SC']='2'
        return Response({'code':'100','game':rows})


def clock():
    current=datetime(2026,10,29,14,0,tzinfo=timezone.utc)
    def tick():
        nonlocal current
        current+=timedelta(milliseconds=1)
        return current
    return tick


def test_current_and_next_day_state_use_actual_observation_time_not_backdating():
    s=Session();result=collect(session=s,clock=clock())
    assert len(s.calls)==2 and not result['errors']
    for day,state in result['daily_states'].items():
        assert state['status']=='available',state
        assert state['seed_historical_availability']=='unproven'
        assert state['available_at']>result['seed_observed_at']
        assert state['state_date']==day
    assert result['daily_states']['2026-10-30']['e0']['total_observations']==2914


def test_empty_correction_cannot_hide_previously_known_game_or_reuse_stale_cache():
    first=collect(session=Session(),clock=clock())
    result=collect(first,session=Session(empty=True),clock=clock())
    assert result['errors']['2026-10-29']=='empty_calendar_contradicts_cached_games'
    assert result['daily_states']['2026-10-30']['status']=='unavailable'


def test_incomplete_today_only_blocks_tomorrow_state():
    result=collect(session=Session(pending=True),clock=clock())
    assert result['daily_states']['2026-10-29']['status']=='available'
    assert result['daily_states']['2026-10-30']['status']=='unavailable'


def test_optional_collector_never_makes_unbounded_historical_requests():
    s=Session()
    result=collect(session=s,clock=lambda:datetime(2028,1,1,tzinfo=timezone.utc))
    assert result['status']=='unavailable' and not s.calls
