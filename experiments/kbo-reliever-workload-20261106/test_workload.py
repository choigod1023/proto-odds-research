from datetime import date, timedelta

from workload import pitcher_rows, workload


def box_table(rows):
    def cells(values):
        return {"row": [{"Text": value} for value in values]}

    return {"headers": [cells(["선수명", "등판", "투구수"])], "rows": [cells(r) for r in rows]}


def test_reads_official_pitch_count_and_distinguishes_relief():
    table = box_table([["100", "선발", "83"], ["구원", "7.1", "18"]])
    assert pitcher_rows(table, 2025, "LG", "fixture", {(2025, "LG", "구원"): {"200"}}) == [
        {"id": "100", "relief": False, "pitches": 83},
        {"id": "200", "relief": True, "pitches": 18},
    ]


def test_previous_days_only_and_unknown_box_not_zero():
    target = date(2026, 4, 10)
    days = {(target - timedelta(days=gap)).isoformat(): {"games": []} for gap in range(11)}

    def game(gap, rows):
        key = (target - timedelta(days=gap)).isoformat()
        days[key]["games"].append({"home": "LG", "away": "KT", "appearances": {"LG": rows}})

    game(4, [{"id": p, "relief": True, "pitches": 10} for p in ("1", "2", "3")])
    game(2, [{"id": "1", "relief": True, "pitches": 20}, {"id": "4", "relief": True, "pitches": 90}])
    game(1, [{"id": "1", "relief": True, "pitches": 12}])
    game(0, [{"id": "2", "relief": True, "pitches": 999}])
    assert workload(days, target, "LG") == [
        {"id": "1", "pitches_3d": 32, "back_to_back": True, "days_since": 1},
        {"id": "2", "pitches_3d": 0, "back_to_back": False, "days_since": 4},
        {"id": "3", "pitches_3d": 0, "back_to_back": False, "days_since": 4},
    ]
    days[(target - timedelta(days=3)).isoformat()]["games"].append({"home": "LG", "away": "KT"})
    assert workload(days, target, "LG") is None
