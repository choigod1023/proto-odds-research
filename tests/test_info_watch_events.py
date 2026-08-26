import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from info_watch import classify_starter_event  # noqa: E402


def test_starter_event_records_first_release_and_change():
    assert classify_starter_event("A", "", observed_before=True) == "first"
    assert classify_starter_event("B", "A", observed_before=True) == "change"
    assert classify_starter_event("A", "A", observed_before=True) is None


def test_initial_existing_value_is_labeled_baseline():
    assert classify_starter_event("A", "", observed_before=False) == "baseline"
