from types import SimpleNamespace
from datetime import datetime, timezone
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import collect


def test_default_collection_years_follow_kst_current_year():
    assert collect.default_years(datetime(2027, 1, 1, tzinfo=timezone.utc)) == [
        2023, 2024, 2025, 2026, 2027]


def test_round_settlement_rejects_pregame_and_in_play(monkeypatch):
    monkeypatch.setattr(collect, "parse_rows", lambda *_: [
        SimpleNamespace(result="홈승", is_void=False),
        SimpleNamespace(result="경기전", is_void=False),
    ])
    assert collect.round_settlement("html", 2026, 1) == (False, 2)

    # 발매 잠금 때문에 odds-derived is_void=True여도 결과가 경기전이면 미정산이다.
    monkeypatch.setattr(collect, "parse_rows", lambda *_: [
        SimpleNamespace(result="경기전", is_void=True),
    ])
    assert collect.round_settlement("html", 2026, 1) == (False, 1)

    monkeypatch.setattr(collect, "parse_rows", lambda *_: [
        SimpleNamespace(result="홈승", is_void=False),
        SimpleNamespace(result="5회초", is_void=False),
    ])
    assert collect.round_settlement("html", 2026, 1) == (False, 2)


def test_round_settlement_accepts_final_and_void_rows(monkeypatch):
    monkeypatch.setattr(collect, "parse_rows", lambda *_: [
        SimpleNamespace(result="홈패", is_void=False),
        SimpleNamespace(result="취소", is_void=True),
    ])
    assert collect.round_settlement("html", 2026, 1) == (True, 2)


def test_legacy_cache_metadata_does_not_invent_collection_time(tmp_path):
    cache = tmp_path / "round.html.gz"
    cache.write_bytes(b"legacy")

    collect._write_cache_meta(cache, complete=True, row_count=10)
    meta_path = cache.with_suffix(cache.suffix + ".meta.json")
    legacy = json.loads(meta_path.read_text(encoding="utf-8"))
    assert legacy["collected_at"] is None
    assert legacy["timing_status"] == "legacy_collection_time_unknown"

    collected_at = "2026-08-26T00:00:00+00:00"
    collect._write_cache_meta(
        cache, complete=True, row_count=10, collected_at=collected_at)
    fetched = json.loads(meta_path.read_text(encoding="utf-8"))
    assert fetched["collected_at"] == collected_at
    assert fetched["timing_status"] == "network_fetch_recorded"
