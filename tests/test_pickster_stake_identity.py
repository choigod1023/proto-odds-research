import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pickster_watch as watch


def html(selection, final=False):
    header = 'NYY 3 FINAL BAL 1' if final else 'NYY 7:05 PM ET BAL'
    return f'''<section class="relative scroll-mt-10"><div></div><div>{header}</div><div>
    <div class="grid grid-cols-[auto_1fr]"><a href="/cappers/alpha">@alpha</a>
    @alpha {selection}</div></div></section>'''


def test_real_stake_suffix_failure():
    before, _ = watch.parse_slate(html('NYY ML -115 5u'), '2026-08-29T10:08:28Z')
    after, _ = watch.parse_slate(html('NYY ML -115 W +4.35u', True), '2026-08-29T21:03:04Z')
    assert before[0]['pick_id'] == after[0]['pick_id']
    assert before[0]['selection'] == 'NYY ML -115'
    assert before[0]['american_odds'] == -115
    assert '5u' in before[0]['raw_text']
    assert before[0]['result'] is None and after[0]['result'] == 'W'


def test_distinct_markets_and_odds_stay_distinct():
    ids = [watch.parse_slate(html(s), '2026-08-29T10:08:28Z')[0][0]['pick_id']
           for s in ['NYY ML -115 5u','NYY ML -120 5u','NYY -1.5 -115 5u']]
    assert len(set(ids)) == 3


def test_migration_preserves_old_state_and_no_backfill(monkeypatch):
    state = {'initialized_at':'old', 'pick_identity_version':2,
             'picks':{'old-id':{'eligible_pre_event':True}}}
    saved, logged = [], []
    monkeypatch.setattr(watch, '_load_state', lambda: state)
    monkeypatch.setattr(watch, '_save_state', lambda value: saved.append(value))
    monkeypatch.setattr(watch, '_append_jsonl', lambda path, rows: logged.extend(rows) or len(rows))
    monkeypatch.setattr(watch, 'parse_leaderboard', lambda *args: [])
    monkeypatch.setattr(watch, '_now', lambda: '2026-08-29T10:08:28Z')
    watch.collect_once(None, leaderboard_html='', slate_html=html('NYY ML -115 5u'))
    assert 'old-id' in saved[0]['picks']
    picks = [r for r in logged if r.get('pick_id')]
    assert picks[0]['event_type'] == 'baseline'
    assert picks[0]['eligible_pre_event'] is False


def test_new_pregame_pick_receives_graded_event(monkeypatch):
    state = {'initialized_at':'old', 'pick_identity_version':4, 'picks':{}}
    logged = []
    monkeypatch.setattr(watch, '_load_state', lambda: state)
    monkeypatch.setattr(watch, '_save_state', lambda value: None)
    monkeypatch.setattr(watch, '_append_jsonl', lambda path, rows: logged.extend(rows) or len(rows))
    monkeypatch.setattr(watch, 'parse_leaderboard', lambda *args: [])
    monkeypatch.setattr(watch, '_now', lambda: '2026-08-29T10:08:28Z')
    watch.collect_once(None, leaderboard_html='', slate_html=html('NYY ML -115 5u'))
    monkeypatch.setattr(watch, '_now', lambda: '2026-08-29T21:03:04Z')
    watch.collect_once(None, leaderboard_html='', slate_html=html('NYY ML -115 W +4.35u', True))
    picks = [r for r in logged if r.get('pick_id')]
    assert [r['event_type'] for r in picks] == ['first_observed','graded']
    assert all(r['eligible_pre_event'] for r in picks)
    assert picks[0]['pick_id'] == picks[1]['pick_id']
