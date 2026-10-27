import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pickster_watch as watch


def html(selection, final=False):
    header = 'NYY 3 FINAL BAL 1' if final else 'NYY 7:05 PM ET BAL'
    return f'''<section class="relative scroll-mt-10"><div></div><div>{header}</div><div>
    <div class="grid grid-cols-[auto_1fr]"><a href="/cappers/alpha">@alpha</a>
    @alpha {selection}</div></div></section>'''


def parse(selection, at, final=False):
    return watch.parse_slate(html(selection, final), at)[0][0]


def test_age_prefix_does_not_split_identity():
    before = parse('5h NYY ML -115 5u', '2026-08-29T10:08:28Z')
    later = parse('6h NYY ML -115 5u', '2026-08-29T11:08:28Z')
    after = parse('9h NYY ML -115 W +4.35u', '2026-08-29T21:03:04Z', True)
    assert before['pick_id'] == later['pick_id'] == after['pick_id']
    assert before['selection'] == 'NYY ML -115'
    assert before['american_odds'] == -115


def test_uppercase_first_half_marker_is_not_age():
    pick = parse('1H Over 4.5 -110 1u', '2026-08-29T10:08:28Z')
    assert pick['selection'] == '1H Over 4.5 -110'


def test_pregame_parlay_leg_is_flagged_and_keeps_identity():
    before = parse('3h NYY ML -172 1u in 5 -leg', '2026-08-29T10:08:28Z')
    after = parse('NYY ML -172 L -1u in 5 -leg', '2026-08-29T21:03:04Z', True)
    assert before['is_parlay_leg'] and before['parlay_legs'] == 5
    assert before['selection'] == 'NYY ML -172'
    assert before['pick_id'] == after['pick_id']
    straight = parse('3h NYY ML -172 1u', '2026-08-29T10:08:28Z')
    assert not straight['is_parlay_leg'] and straight['pick_id'] == before['pick_id']


def _collector(monkeypatch, state, logged):
    monkeypatch.setattr(watch, '_load_state', lambda: state)
    monkeypatch.setattr(watch, '_save_state', lambda value: None)
    monkeypatch.setattr(watch, '_append_jsonl', lambda path, rows: logged.extend(rows) or len(rows))
    monkeypatch.setattr(watch, 'parse_leaderboard', lambda *args: [])

    def run(at, selection, final=False):
        monkeypatch.setattr(watch, '_now', lambda: at)
        watch.collect_once(None, leaderboard_html='', slate_html=html(selection, final))
    return run


def _state():
    return {'initialized_at': 'old', 'pick_identity_version': 4, 'picks': {}}


def pick_events(logged):
    return [r for r in logged if r.get('pick_id')]


def test_graded_row_after_et_midnight_joins_previous_day_pick(monkeypatch):
    logged = []
    run = _collector(monkeypatch, _state(), logged)
    run('2026-08-29T10:08:28Z', '5h NYY ML -115 5u')
    run('2026-08-29T23:30:00Z', 'NYY ML -115 5u', True)
    # 동부 01:10: 전날 슬레이트가 남아 있어 slate_date가 08-30으로 계산된다.
    run('2026-08-30T05:10:00Z', 'NYY ML -115 W +4.35u', True)
    run('2026-08-30T05:25:00Z', 'NYY ML -115 W +4.35u', True)
    events = pick_events(logged)
    assert len({r['pick_id'] for r in events}) == 1
    assert [r['event_type'] for r in events] == ['first_observed', 'changed', 'graded']
    assert events[-1]['slate_date'] == '2026-08-29'
    assert events[-1]['result'] == 'W'
    assert all(r['eligible_pre_event'] for r in events)


def test_late_morning_and_pregame_rows_are_not_joined(monkeypatch):
    logged = []
    run = _collector(monkeypatch, _state(), logged)
    run('2026-08-29T10:08:28Z', 'NYY ML -115 5u')
    run('2026-08-29T23:30:00Z', 'NYY ML -115 5u', True)
    # 동부 10시 이후: 다음 날 시리즈 경기 결과일 수 있어 옮기지 않는다.
    run('2026-08-30T14:30:00Z', 'NYY ML -115 W +4.35u', True)
    events = pick_events(logged)
    assert len({r['pick_id'] for r in events}) == 2
    assert events[-1]['eligible_pre_event'] is False

    logged.clear()
    run = _collector(monkeypatch, _state(), logged)
    run('2026-08-29T23:30:00Z', 'NYY ML -115 5u', True)
    run('2026-08-30T05:10:00Z', 'NYY ML -115 5u')  # 다음 날 경기 전 관측
    assert len({r['pick_id'] for r in pick_events(logged)}) == 2


def test_unstarted_candidate_is_not_graded_by_next_day_row(monkeypatch):
    logged = []
    run = _collector(monkeypatch, _state(), logged)
    run('2026-08-29T22:00:00Z', 'NYY ML -115 5u')  # 경기 시작을 관측하지 못한 미채점 픽
    run('2026-08-30T05:10:00Z', 'NYY ML -115 W +4.35u', True)
    events = pick_events(logged)
    assert len({r['pick_id'] for r in events}) == 2
    assert events[-1]['eligible_pre_event'] is False
