import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import player_info  # noqa: E402
from detail_paths import latest_detail_path  # noqa: E402
from player_info import XFIP_SHRINK_IP, kbo_pitcher_stats  # noqa: E402


def _pitcher(name, *, inn, er, hr, bb, kk, hit, pcode=None):
    return {
        "name": name, "inn": inn, "er": er, "hr": hr,
        "bb": bb, "kk": kk, "hit": hit, "pcode": pcode,
    }


def test_looping_collector_rechecks_detail_cache_after_year_rollover(
        tmp_path, monkeypatch):
    def write_year(year, name):
        path = tmp_path / f"kbo_baseball_2023_{year}.json"
        path.write_text(json.dumps({
            "g1": {
                "date": f"{year}-01-01", "home": "홈", "away": "원정",
                "data": {
                    "home": [_pitcher(name, inn="6", er=1, hr=0, bb=1, kk=6, hit=4)],
                    "away": [_pitcher("상대", inn="6", er=2, hr=1, bb=1, kk=5, hit=5)],
                },
            },
        }, ensure_ascii=False), encoding="utf-8")
        return path

    write_year(2026, "이전선발")
    monkeypatch.setattr(
        player_info, "latest_detail_path",
        lambda league, kind: latest_detail_path(league, kind, root=tmp_path))
    assert "이전선발" in player_info.kbo_pitcher_stats()

    write_year(2027, "새해선발")
    refreshed = player_info.kbo_pitcher_stats()
    assert "새해선발" in refreshed
    assert "이전선발" not in refreshed


def test_kbo_xfip_matches_offline_shrinkage_and_records_sample_cutoff(tmp_path):
    detail = tmp_path / "kbo.json"
    detail.write_text(json.dumps({
        "g1": {
            "date": "2026-08-01",
            "data": {
                "home": [_pitcher("에이스", inn="9", er=4, hr=2, bb=3, kk=12, hit=7)],
                "away": [_pitcher("상대", inn="9", er=5, hr=0, bb=1, kk=4, hit=8)],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")

    stats = kbo_pitcher_stats(detail)["에이스"]

    # 리그 HR/9=1, FIP 상수=4.1666...인 자료에서 오프라인 수식 결과.
    assert stats["xfip"] == 4.21
    assert stats["sample_ip"] == 9.0
    assert stats["stats_as_of"] == "2026-08-01"
    assert stats["xfip_shrink_ip"] == XFIP_SHRINK_IP == 40.0
    assert stats["xfip_league_hr9"] == 1.0
    assert stats["xfip_approx"] is True


def test_kbo_xfip_keeps_latest_twelve_starts_and_latest_used_date(tmp_path):
    games = {}
    for day in range(1, 14):
        games[f"g{day}"] = {
            "date": f"2026-07-{day:02d}",
            "data": {
                "home": [_pitcher("에이스", inn="5", er=1, hr=0, bb=1, kk=5, hit=4)],
                "away": [_pitcher(f"상대{day}", inn="5", er=2, hr=1, bb=1, kk=4, hit=5)],
            },
        }
    detail = tmp_path / "kbo.json"
    detail.write_text(json.dumps(games, ensure_ascii=False), encoding="utf-8")

    stats = kbo_pitcher_stats(detail)["에이스"]

    assert stats["games_started"] == 12
    assert stats["sample_ip"] == 60.0
    assert stats["stats_as_of"] == "2026-07-13"


def test_kbo_stats_fail_closed_instead_of_treating_missing_counts_as_zero(tmp_path):
    detail = tmp_path / "kbo.json"
    malformed_pitcher = _pitcher("결측선발", inn="6", er=1, hr=0, bb=2, kk=6, hit=5)
    del malformed_pitcher["hr"]
    detail.write_text(json.dumps({
        "g1": {
            "date": "2026-08-01",
            "data": {
                "home": [malformed_pitcher],
                "away": [_pitcher("정상선발", inn="6", er=2, hr=1, bb=1, kk=5, hit=6)],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")

    stats = kbo_pitcher_stats(detail)

    assert "결측선발" not in stats
    assert "정상선발" in stats
    assert kbo_pitcher_stats(tmp_path / "missing.json") == {}


def test_kbo_stats_rejects_unexpected_json_shape(tmp_path):
    detail = tmp_path / "kbo.json"
    detail.write_text("[]", encoding="utf-8")

    assert kbo_pitcher_stats(detail) == {}

    detail.write_text(json.dumps({
        "g1": {
            "date": "2026-08-01",
            "data": {
                "home": [_pitcher("비정상", inn="6", er=1, hr="NaN", bb=2, kk=6, hit=5)],
                "away": [],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    assert kbo_pitcher_stats(detail) == {}


def test_kbo_stats_do_not_mix_same_name_with_different_player_ids(tmp_path):
    detail = tmp_path / "kbo.json"
    detail.write_text(json.dumps({
        "g1": {
            "date": "2026-08-01", "home": "A팀", "away": "상대1",
            "data": {
                "home": [_pitcher("동명이인", pcode="111", inn="6", er=0,
                                    hr=0, bb=0, kk=9, hit=2)],
                "away": [_pitcher("상대", pcode="901", inn="6", er=2,
                                    hr=1, bb=1, kk=5, hit=5)],
            },
        },
        "g2": {
            "date": "2026-08-02", "home": "B팀", "away": "상대2",
            "data": {
                "home": [_pitcher("동명이인", pcode="222", inn="3", er=6,
                                    hr=3, bb=4, kk=1, hit=8)],
                "away": [_pitcher("상대", pcode="902", inn="6", er=2,
                                    hr=1, bb=1, kk=5, hit=5)],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")

    stats = kbo_pitcher_stats(detail)

    assert "동명이인" not in stats
    assert stats[("A팀", "동명이인")]["player_id"] == "111"
    assert stats[("B팀", "동명이인")]["player_id"] == "222"
    assert stats[("A팀", "동명이인")]["games_started"] == 1
    assert stats[("B팀", "동명이인")]["games_started"] == 1
