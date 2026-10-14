import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from runtime_db import RuntimeDatabase


def test_context_sections_share_snapshot_during_concurrent_publish(tmp_path):
    db = RuntimeDatabase(tmp_path / 'snapshot.sqlite3')
    db.store_artifact('picks_v2', {'live': [{'home': 'old-live'}],
                                 'past': [{'home': 'old-past'}]})
    rows = db.iter_recommendation_context()
    assert next(rows)['home'] == 'old-live'
    db.store_artifact('picks_v2', {'live': [{'home': 'new-live'}],
                                 'past': [{'home': 'new-past'}]})
    assert [row['home'] for row in rows] == ['old-past']
    assert [row['home'] for row in db.iter_recommendation_context()] == ['new-live', 'new-past']
