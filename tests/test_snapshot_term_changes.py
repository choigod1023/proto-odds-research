"""Synthetic snapshot regression; never historical profit evidence."""
import csv
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import snapshot
from runtime_db import RuntimeDatabase


def record(**changes):
    return dict(dict(ts='2026-11-06T09:00:00+00:00', year=2026, round=132,
        game_no='2855', sport='bk', league='WKBL', market_family='언더오버', n_way=2,
        market_label='U 135.5', home='하나은행', away='KB스타즈',
        date_text='11.06(금) 19:00', odds='1.76,1.72', result='경기전'), **changes)


@pytest.mark.parametrize('changes,kind', [
    ({'market_label': 'U 137.5'}, 'line'),
    ({'odds': '1.80,1.70'}, 'price'),
    ({'odds': '1.80,1.70', 'market_label': 'U 137.5'}, 'price_and_line'),
])
def test_price_and_line_moves_are_separate(changes, kind):
    before, after = record(), record(**changes)
    change = snapshot._snapshot_change(before, after)
    assert change['change_type'] == kind
    assert change['prev_odds'] == before['odds']
    assert change['prev_market_label'] == 'U 135.5'
    assert before == record() and after == record(**changes)


@pytest.mark.parametrize('changes', [
    {}, {'market_label': 'U 135.50'}, {'ts': '2026-11-06T09:10:00+00:00'},
    {'result': '오버'}, {'market_label': '미확인'},
])
def test_no_numeric_move_does_not_invent_change(changes):
    assert snapshot._snapshot_change(record(), record(**changes)) is None


@pytest.mark.parametrize('field,value', [
    ('date_text', '11.07(토) 19:00'), ('away', '다른팀'), ('home', '다른팀'),
    ('league', 'KBL'), ('sport', 'bs'), ('market_family', '핸디캡'),
    ('n_way', 3), ('round', 131), ('year', 2025), ('game_no', '9999'),
])
def test_reused_number_or_changed_fixture_is_not_a_price_move(field, value):
    assert snapshot._snapshot_change(record(), record(**{field:value, 'odds':'1.80,1.70'})) is None


@pytest.mark.parametrize('sqlite', [True, False])
def test_snap_logs_same_price_new_line_and_retry_does_not_duplicate(tmp_path, monkeypatch, sqlite):
    if sqlite:
        monkeypatch.setenv('PROODD_DB_PATH', str(tmp_path / 'runtime.db'))
    else:
        monkeypatch.delenv('PROODD_DB_PATH', raising=False)
    monkeypatch.setattr(snapshot, 'OUT', tmp_path / 'snapshots')
    monkeypatch.setattr(snapshot, 'CH_FILE', tmp_path / 'snapshots/changes.csv')
    monkeypatch.setattr(snapshot, '_session', object)
    monkeypatch.setattr(snapshot.time, 'sleep', lambda *_: None)
    clock = [datetime.now(timezone.utc).replace(microsecond=0)]
    label = ['U 135.5']

    def fetch(*_):
        r = record(market_label=label[0])
        row = SimpleNamespace(**{k:v for k,v in r.items() if k not in ('ts','odds')}, odds=[1.76,1.72])
        return [row], clock[0], b'synthetic body'

    monkeypatch.setattr(snapshot, '_fetch_observation', fetch)
    assert snapshot.snap(2026, [132]) == 0
    label[0] = 'U 137.5'
    from datetime import timedelta
    clock[0] += timedelta(seconds=10)
    assert snapshot.snap(2026, [132]) == 1
    clock[0] += timedelta(seconds=10)
    assert snapshot.snap(2026, [132]) == 0
    assert snapshot._load_last(2026, 132)[('2026','132','2855')] == '1.76,1.72'
    if sqlite:
        db = RuntimeDatabase()
        change, = db.events('odds_changes')
        assert change['change_type'] == 'line' and not change['price_changed']
        assert change['prev_market_label'] == 'U 135.5' and change['market_label'] == 'U 137.5'
        assert db.counts()['odds_snapshots'] == 3
    else:
        with snapshot.CH_FILE.open(encoding='utf-8', newline='') as stream:
            change, = list(csv.DictReader(stream))
        assert change['prev_odds'] == change['odds'] == '1.76,1.72'
        assert change['market_label'] == 'U 137.5'
