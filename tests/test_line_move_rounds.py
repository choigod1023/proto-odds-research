import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from line_move import from_rounds  # noqa: E402
from stack_filter import WIN_IDX  # noqa: E402


HOME_RESULT = next(result for (n_way, result), winner in WIN_IDX.items()
                   if n_way == 2 and winner == 0)
AWAY_RESULT = next(result for (n_way, result), winner in WIN_IDX.items()
                   if n_way == 2 and winner == 1)


def _row(round_no: int, game_no: int, kickoff: str, odds: str,
         *, result: str = HOME_RESULT, home: str = "A 3", away: str = "1 B",
         family: str = "win_loss", n_way: int = 2, label: str = "") -> dict:
    return {
        "year": 2026, "round": round_no, "game_no": game_no,
        "date_text": kickoff, "sport": "bs", "league": "KBO",
        "market_family": family, "market_label": label, "n_way": n_way,
        "home": home, "away": away, "odds": odds, "result": result,
        "is_void": False,
    }


def test_round_overlap_keeps_full_kickoff_and_does_not_mix_doubleheader():
    rows = [
        _row(10, 101, "08.01(토) 14:00", "1.80,1.80"),
        _row(11, 201, "08.01(토) 18:00", "1.70,1.90"),
    ]

    assert from_rounds(pd.DataFrame(rows)).empty


def test_round_overlap_matches_same_game_and_normalizes_decimal_score_tokens():
    rows = [
        _row(10, 101, "08.01(토) 14:00", "1.80,1.80",
             home="A -1.5", away="2.5 B", family="handicap", label="H -1.5"),
        _row(11, 201, "08.01(토) 14:00", "1.70,1.90",
             home="A -2.5", away="1.5 B", family="handicap", label="H -1.5"),
    ]

    got = from_rounds(pd.DataFrame(rows))

    assert len(got) == 2
    assert set(got["odds"]) == {1.7, 1.9}
    assert got["key"].str.contains("2026-08-01T14:00:00", regex=False).all()


def test_round_overlap_requires_compatible_market_shape():
    rows = [
        _row(10, 101, "08.01(토) 14:00", "1.80,1.80", n_way=2),
        _row(11, 201, "08.01(토) 14:00", "2.20,3.10,2.40",
             n_way=3, family="three_way"),
    ]

    assert from_rounds(pd.DataFrame(rows)).empty


def test_round_overlap_fails_closed_on_conflicting_slot_or_result():
    conflicting_slot = pd.DataFrame([
        _row(10, 101, "08.01(토) 14:00", "1.80,1.80"),
        _row(10, 102, "08.01(토) 14:00", "1.60,2.00"),
        _row(11, 201, "08.01(토) 14:00", "1.70,1.90"),
    ])
    conflicting_result = pd.DataFrame([
        _row(10, 101, "08.01(토) 14:00", "1.80,1.80", result=HOME_RESULT),
        _row(11, 201, "08.01(토) 14:00", "1.70,1.90", result=AWAY_RESULT),
    ])

    assert from_rounds(conflicting_slot).empty
    assert from_rounds(conflicting_result).empty
