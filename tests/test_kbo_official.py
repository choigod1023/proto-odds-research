from pathlib import Path
import sys
from datetime import datetime, timezone
import json
import requests
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kbo_official import collect_official

NOW = datetime(2026, 10, 27, 3, tzinfo=timezone.utc)
GAME = dict(LE_ID=1, SR_ID=5, SEASON_ID=2026, G_DT='20261027', G_TM='18:30',
            G_ID='20261027HTSS0', HOME_ID='SS', AWAY_ID='HT', HOME_NM='삼성', AWAY_NM='KIA',
            GAME_STATE_SC='1', CANCEL_SC_ID='0', START_PIT_CK=1,
            B_PIT_P_NM='후라도 ', T_PIT_P_NM='올러 ')


def lineup(announced=True):
    table = json.dumps({'rows': [{'row': [{'Text': str(i)}, {'Text': '타자'}, {'Text': '선수' + str(i)}]}
                                  for i in range(1, 10)]})
    return [[{'LINEUP_CK': announced}], [{'G_ID': GAME['G_ID'], 'T_ID': 'SS'}],
            [{'G_ID': GAME['G_ID'], 'T_ID': 'HT'}], [table], [table]]


class Response:
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return self.value


class Session:
    def __init__(self, body=None): self.body = lineup() if body is None else body
    def post(self, url, data, timeout):
        assert timeout == (3, 10)
        if 'GetKboGameList' in url:
            return Response({'code': '100', 'game': [GAME] if data['date'] == '20261027' else []})
        if isinstance(self.body, Exception): raise self.body
        return Response(self.body)


def test_today_official_postseason_and_home_away_mapping():
    doc = collect_official(Session(), NOW, {})
    row = doc['games'][0]
    assert row['starters']['home']['name'] == '후라도'
    assert row['starters']['away']['name'] == '올러'
    assert row['game_datetime'] == '2026-10-27T18:30:00+09:00'
    assert row['lineup_status']['official_today']
    assert len(row['lineups']['away']) == 9


def test_unannounced_previous_game_lineup_never_imported():
    body = lineup(False)
    body[1][0]['G_ID'] = '20261026HTSS0'
    row = collect_official(Session(body), NOW, {})['games'][0]
    assert row['lineups'] == {}
    assert row['lineup_status']['side_states']['home'] == 'not_announced'


@pytest.mark.parametrize('change', ['date', 'team', 'duplicate', 'missing', 'envelope'])
def test_invalid_announced_lineup_rejected(change):
    body = lineup()
    if change == 'date': body[1][0]['G_ID'] = '20261026HTSS0'
    if change == 'team': body[1][0]['T_ID'] = 'HT'
    if change in ('duplicate', 'missing'):
        table = json.loads(body[3][0])
        if change == 'duplicate': table['rows'][0]['row'][0]['Text'] = '2'
        else: table['rows'].pop()
        body[3][0] = json.dumps(table)
    if change == 'envelope': body = {}
    doc = collect_official(Session(body), NOW, {})
    assert doc['errors'] == 1
    assert doc['games'][0]['lineups'] == {}


def test_partial_failure_retains_old_lineup_and_clock_across_source_ids():
    old = collect_official(Session(), NOW, {})
    old['games'][0]['game_id'] = 'naver-id'
    doc = collect_official(Session(requests.Timeout()), NOW.replace(hour=4), old)
    row = doc['games'][0]
    assert row['lineups'] == old['games'][0]['lineups']
    assert row['lineup_status']['observed_at'] == old['generated_at']
    assert row['lineup_status']['side_states']['home'] == 'stale'
    assert doc['errors'] == 1


def test_schedule_error_html_or_wrong_day_cannot_erase_previous():
    class Bad(Session):
        def post(self, *args, **kwargs): return Response({'code': '200', 'msg': 'error'})
    with pytest.raises(ValueError): collect_official(Bad(), NOW, {})


def test_official_failure_uses_naver_and_never_hides_double_failure(monkeypatch):
    import kbo_official, kbo_pregame
    def fail(**kwargs): raise requests.Timeout()
    monkeypatch.setattr(kbo_official, 'collect_official', fail)
    monkeypatch.setattr(kbo_pregame, 'collect', lambda **kwargs: {'games': [], 'errors': 0})
    assert kbo_pregame.collect_available(NOW, {})['route'] == 'naver_fallback'
    monkeypatch.setattr(kbo_pregame, 'collect', fail)
    with pytest.raises(requests.Timeout): kbo_pregame.collect_available(NOW, {})
