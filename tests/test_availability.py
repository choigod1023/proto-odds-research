from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from availability import enrich_availability  # noqa: E402


def test_reason_and_player_weight_are_separate():
    info = enrich_availability({
        "sport": "sc",
        "key_players": {"home": [{"name": "주전공격수"}], "away": []},
        "unavailable": {
            "home": [{"name": "주전공격수", "status": "경고 누적 출장 정지",
                      "source_type": "official_discipline"}],
            "away": [{"name": "후보", "status": "출전 의심",
                      "source_type": "media"}],
        },
    })
    home, away = info["unavailable"]["home"][0], info["unavailable"]["away"][0]
    assert home["reason_code"] == "cards"
    assert home["availability_probability"] == 1
    assert home["impact_score"] > away["impact_score"]
    assert info["availability_summary"]["leans"] == "away"
    assert info["availability_summary"]["model_adjustment"] == 0


def test_bench_player_is_not_treated_as_certain_absence():
    info = enrich_availability({
        "sport": "sc", "key_players": {},
        "unavailable": {"home": [{"name": "A", "status": "벤치 시작",
                                    "source_type": "official_lineup"}]},
    })
    assert info["unavailable"]["home"][0]["availability_probability"] == .1
