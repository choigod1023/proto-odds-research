"""Regression fixtures are explicit, disposable archives, never operational data."""
import json
import sys

import numpy as np
import pandas as pd
import pytest

from src import nonlinear_features as nf
from src.devig import market_probabilities


def game(day="08.01(토) 18:00", **changes):
    row = dict(year=2026, round=90, game_no="001", date_text=day, sport="bs",
               league="KBO", market_tag="hm", market_label="일반", market_family="승패",
               booking_class="2-way", market_type="승패(2-way)", n_way=2,
               home="A 3", away="1 B", odds="1.50,2.50", overround="1.066667",
               result="홈승", is_void=False)
    row.update(changes)
    return row


def load(tmp_path, rows):
    path = tmp_path / "archive.csv"
    pd.DataFrame(rows, columns=list(game())).to_csv(path, index=False)
    return nf.load_research_rows(path)


def market(family, label, **changes):
    row = dict(market_family=family, market_label=label, market_type=family,
               market_tag={"언더오버": "un", "핸디캡": "hp"}.get(family, "hm"), home="A", away="B",
               result="언더" if family == "언더오버" else "핸디승")
    row.update(changes)
    return row


def test_favorite_schema_shin_ties_and_no_outcome_features(tmp_path):
    rows = [game(odds="1.76,1.76", overround="999"),
            game("08.02(일) 18:00", odds="2.50,1.50", result="홈패", home="A 1", away="3 B"),
            game("08.03(월) 18:00", **market("승무패", "승무패", sport="sc", n_way=3,
                 odds="3.00,1.60,3.00", result="무승부", home="A 1", away="1 B")),
            game("08.04(화) 18:00", **market("언더오버", "U 2.5", odds="2.50,1.50", result="오버"))]
    frame, meta = load(tmp_path, rows)
    assert frame.sel.tolist() == ["홈승", "홈패", "무승부", "오버"]
    assert frame.winner.tolist() == [0, 1, 1, 1]
    assert frame.y.tolist() == [1., 1., 1., 1.]
    assert frame.direction.tolist() == [1, -1, 0, -1]
    assert frame.iloc[0].q == pytest.approx(.5)
    assert frame.iloc[0].favorite_gap == pytest.approx(0)
    assert frame.iloc[0].overround == pytest.approx(2 / 1.76)
    assert frame.iloc[1].q == pytest.approx(market_probabilities([2.5, 1.5])[1])
    assert frame.row_id.is_unique
    assert frame.kickoff.dt.tz is None
    assert pd.api.types.is_integer_dtype(frame.n_way)
    assert pd.api.types.is_bool_dtype(frame.is_void)
    for name in nf.MARKET_FEATURES + nf.TEAM_FEATURES:
        assert pd.api.types.is_numeric_dtype(frame[name])
    assert not {"result", "y", "winner", "is_void", "row_id", "event_key"} & set(
        nf.MARKET_FEATURES + nf.TEAM_FEATURES)
    json.dumps(meta, allow_nan=False)


def test_reissues_preserve_exact_line_nway_kickoff_and_stable_ids(tmp_path):
    rows = [game(), game(round=91, game_no="099"),
            game(**market("핸디캡", "H -1.50")),
            game(round=91, **market("핸디캡", "H -1.5")),
            game(**market("핸디캡", "H -1.5001")),
            game(**market("핸디캡", "H +1.5")),
            game(**market("핸디캡", "H -1.5", n_way=3, odds="1.5,3.0,4.0")),
            game("08.01(토) 21:00"), game("08.02(일) 18:00")]
    frame, meta = load(tmp_path, rows)
    assert len(frame) == 7
    assert meta["duplicate_reissues_removed"] == 2
    assert frame.event_key.nunique() == 3
    assert set(frame[frame.market == "핸디캡"].line) == {-1.5, -1.5001, 1.5}
    assert set(frame[frame.market == "핸디캡"].n_way) == {2, 3}
    reversed_frame, _ = load(tmp_path, rows[::-1])
    pd.testing.assert_frame_equal(frame, reversed_frame)
    revised, _ = load(tmp_path, [{**rows[0], "odds": "1.4,2.8", "result": "홈패",
                                 "home": "A 1", "away": "3 B"}])
    base = frame[(frame.market == "승패") & (frame.kickoff == pd.Timestamp("2026-08-01 18:00"))].iloc[0]
    assert revised.iloc[0].row_id == base.row_id


@pytest.mark.parametrize("change", [dict(odds="1.4,2.8"), dict(result="홈패"),
                                    dict(is_void=True, result="취소")])
def test_conflicting_reissues_remove_whole_market_and_score_history(tmp_path, change):
    frame, meta = load(tmp_path, [game(), game(round=91, **change),
                                 game(**market("언더오버", "U 4.5")),
                                 game("08.02(일) 18:00")])
    assert len(frame) == 2
    assert meta["conflicting_reissues_excluded"] == 1
    assert meta["conflicting_rows_excluded"] == 2
    assert frame.iloc[-1].own_sample_count == 0
    reverse, _ = load(tmp_path, [game("08.02(일) 18:00"),
                                game(**market("언더오버", "U 4.5")),
                                game(round=91, **change), game()])
    pd.testing.assert_frame_equal(frame, reverse)


@pytest.mark.parametrize("flag,result", [(True, "취소"), ("true", "무효"),
                                        ("1", "홈승"), (False, "연기")])
def test_void_retained_without_target_or_score_update(tmp_path, flag, result):
    frame, meta = load(tmp_path, [game(is_void=flag, result=result), game("08.02(일) 18:00")])
    assert len(frame) == 2
    assert bool(frame.iloc[0].is_void)
    assert frame.iloc[0].winner == -1
    assert pd.isna(frame.iloc[0].y)
    assert frame.iloc[0].odds == 1.5
    assert frame.iloc[1].own_sample_count == 0
    assert meta["void_rows"] == 1


@pytest.mark.parametrize("odds", ["nan,2.0", "inf,2.0", "1.0,2.0", "0,2.0", "-2,2",
                                 "bad,2", "1.5", "1.5,2,3", "1.5,"])
def test_invalid_odds_explicitly_excluded_even_when_void(tmp_path, odds):
    frame, meta = load(tmp_path, [game(odds=odds, is_void=True), game("08.02(일) 18:00")])
    assert len(frame) == 1
    assert meta["skipped_rows"] == {"invalid_odds": 1}
    assert frame.iloc[0].own_missing == 1


@pytest.mark.parametrize("change,reason", [
    (dict(market_family="승①패"), "unsupported_market"),
    (dict(market_family="승⑤패"), "unsupported_market"),
    (dict(market_family="홀짝"), "unsupported_market"),
    (dict(sport="vl"), "unsupported_sport"),
    (dict(market_family="전반승패"), "partial_period"),
    (dict(market_label="h U 4.5"), "partial_period"),
    (dict(market_label="h(전반)"), "partial_period"),
    (dict(market_type="후반승패"), "partial_period"),
    (dict(market_type="1Q"), "partial_period"),
    (dict(market_label="1세트"), "partial_period"),
    (dict(market_label="5이닝"), "partial_period"),
    (dict(market_label="SUM"), "unsupported_market_label"),
    (dict(market_type="홀짝(2-way)"), "conflicting_market_type"),
    (dict(market_tag="d1"), "conflicting_market_tag"),
    (dict(result="경기전"), "unsettled_or_invalid_result"),
    (dict(result="언더"), "unsettled_or_invalid_result"),
    (dict(n_way=3), "unsupported_market"),
    (dict(is_void="unknown"), "invalid_void_flag"),
    (dict(date_text="08.01(토)"), "invalid_event"),
    (dict(date_text="08.01(토) 24:00"), "invalid_event"),
    (dict(date_text="08.01(토) 18:60"), "invalid_event"),
    (dict(date_text="02.30(월) 18:00"), "invalid_event"),
    (market("핸디캡", "H ?"), "invalid_line"),
    (market("언더오버", "U -1.5"), "invalid_line"),
])
def test_exclusions_are_explicit(tmp_path, change, reason):
    frame, meta = load(tmp_path, [game(**change)])
    assert frame.empty
    assert meta["skipped_rows"] == {reason: 1}
    assert set(nf.MARKET_FEATURES + nf.TEAM_FEATURES) <= set(frame)


def test_next_kst_day_leakage_same_day_doubleheader_and_future_mutation(tmp_path):
    rows = [game("08.01(토) 00:01"), game("08.01(토) 23:59", home="A 5", away="2 B"),
            game("08.02(일) 00:00", home="A 0", away="4 B", result="홈패"),
            game("08.03(월) 18:00", home="A 8", away="2 B")]
    frame, _ = load(tmp_path, rows)
    assert frame.own_sample_count.tolist() == [0, 0, 2, 3]
    assert frame.iloc[2].own_scored_mean == 4
    assert frame.iloc[2].own_scored_std == 1
    assert frame.iloc[2].own_conceded_mean == 1.5
    assert frame.iloc[2].own_form == 1
    assert frame.iloc[2].own_rest_days == 1
    changed, _ = load(tmp_path, rows[:2] + [{**row, "home": "A 99", "away": "0 B", "result": "홈승"}
                                          for row in rows[2:]])
    pd.testing.assert_frame_equal(frame.iloc[:3][nf.TEAM_FEATURES],
                                  changed.iloc[:3][nf.TEAM_FEATURES])


@pytest.mark.parametrize("past", [
    game(**market("핸디캡", "H -1.5", home="A 9", away="1 B")),
    game(**market("언더오버", "U 4.5", home="A 9", away="1 B")),
    game(home="A", away="B"), game(home="A 3.5", away="1 B"),
    game(home="A -1", away="1 B", result="홈패"),
    game(home="A 3", away="1 B", result="홈패"),
    game(home="A 3", away="3 B", result="홈승"),
])
def test_no_fabricated_history_but_markets_retained(tmp_path, past):
    frame, _ = load(tmp_path, [past, game("08.02(일) 18:00")])
    assert len(frame) == 2
    assert frame.iloc[-1].own_missing == 1
    assert pd.isna(frame.iloc[-1].own_scored_mean)


@pytest.mark.parametrize("change", [dict(home="A 4"), dict(result="홈패"),
                                    dict(home="A 0", away="4 B", odds="nan,2")])
def test_score_conflicts_cannot_hide_in_reissues_or_other_moneyline_family(tmp_path, change):
    conflicting = game(market_family="승무패", market_label="승무패", market_type="승무패",
                       n_way=3, odds="1.5,3,4")
    conflicting.update(change)
    frame, meta = load(tmp_path, [game(), conflicting, game("08.02(일) 18:00")])
    assert frame.iloc[-1].own_sample_count == 0
    assert meta["score_history_conflicts"] == 1


def test_last20_and_180_calendar_day_window(tmp_path):
    start = pd.Timestamp("2026-01-01")
    rows = [game((start + pd.Timedelta(days=i)).strftime("%m.%d 18:00"),
                 home=f"A {i + 2}", away="1 B") for i in range(25)]
    frame, _ = load(tmp_path, rows + [game("01.26 18:00")])
    last = frame.iloc[-1]
    assert last.own_sample_count == 20
    assert last.own_scored_mean == np.mean(np.arange(7, 27))
    assert last.own_rest_days == 1
    boundary = start + pd.Timedelta(days=180)
    frame, _ = load(tmp_path, [game("01.01 18:00"),
                               game(boundary.strftime("%m.%d 00:00"), home="A", away="B"),
                               game((boundary + pd.Timedelta(days=1)).strftime("%m.%d 00:00"))])
    assert frame.own_sample_count.tolist() == [0, 1, 0]
    assert frame.iloc[1].own_rest_days == 180
    assert pd.isna(frame.iloc[2].own_rest_days)


def test_team_namespace_orientation_and_numeric_names(tmp_path):
    rows = [game(home="1860 뮌헨 3", away="1 샬케04"),
            game("08.02 18:00", home="1860 뮌헨", away="샬케04", odds="2.5,1.5", result="홈패"),
            game("08.02 18:00", home="1860 뮌헨", away="샬케04", league="OTHER"),
            game("08.02 18:00", home="1860 뮌헨", away="샬케04", sport="bk"),
            game("08.02 18:00", home="1860 뮌헨", away="샬케05")]
    frame, _ = load(tmp_path, rows)
    known = frame[(frame.sel == "홈패")].iloc[0]
    assert known.own_scored_mean == 1
    assert known.opponent_scored_mean == 3
    assert known.scored_mean_diff == -2
    assert known.own_form == 0
    assert frame[frame.league == "OTHER"].iloc[0].own_sample_count == 0
    assert frame[frame.sport == "bk"].iloc[0].own_sample_count == 0
    partial = frame[(frame.sport == "bs") & (frame.league == "KBO") & (frame.sel == "홈승")].iloc[-1]
    assert partial.own_sample_count == 1
    assert partial.opponent_missing == 1
    assert nf._team("1 샬케 04", False) == "샬케 04"


def test_actual_year_and_archive_only_even_when_runtime_enabled(tmp_path, monkeypatch):
    path = tmp_path / "explicit.csv"
    pd.DataFrame([game("12.31(수) 23:59", year=2026, round=1),
                  game("01.01(목) 00:00", year=2026, round=1)]).to_csv(path, index=False)
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "must-not-create.sqlite"))
    monkeypatch.setitem(sys.modules, "runtime_db", None)
    monkeypatch.setitem(sys.modules, "src.runtime_db", None)
    original_read_csv = pd.read_csv
    reads = []

    def read_csv(explicit, *args, **kwargs):
        assert explicit == path
        reads.append(explicit)
        return original_read_csv(explicit, *args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", read_csv)
    frame, _ = nf.load_research_rows(path)
    assert reads == [path]
    assert frame.kickoff.tolist() == [pd.Timestamp("2025-12-31 23:59"), pd.Timestamp("2026-01-01")]
    assert frame.iloc[1].own_sample_count == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["explicit.csv"]


def test_explicit_missing_path_and_schema_fail_without_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        nf.load_research_rows(tmp_path / "missing.csv")
    path = tmp_path / "wrong.csv"
    pd.DataFrame({"year": [2026]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="Archive CSV missing columns"):
        nf.load_research_rows(path)
    empty, meta = load(tmp_path, [])
    assert empty.empty
    assert meta["input_rows"] == 0
    assert pd.api.types.is_datetime64_any_dtype(empty.kickoff)


def test_conflict_accounting_is_order_independent(tmp_path):
    rows = [game(), game(odds="1.4,2.8"), game(odds="1.4,2.8"),
            game("08.02 18:00"), game("08.02 18:00")]
    _, meta = load(tmp_path, rows)
    _, reverse_meta = load(tmp_path, rows[::-1])
    assert meta == reverse_meta
    assert meta["input_rows"] == (meta["output_rows"] + meta["duplicate_reissues_removed"]
                                  + meta["conflicting_rows_excluded"] + sum(meta["skipped_rows"].values()))


def test_extreme_finite_prices_do_not_produce_infinite_features(tmp_path):
    frame, _ = load(tmp_path, [game(odds="1.000000000001,1e300")])
    assert len(frame) == 1
    assert np.isfinite(frame[nf.MARKET_FEATURES].to_numpy()).all()


def test_all_same_event_markets_share_history_without_repeated_updates(tmp_path):
    rows = [game(), game(round=91), game(**market("핸디캡", "H -1.5")),
            game("08.02 18:00"), game("08.02 18:00", **market("언더오버", "U 4.5")),
            game("08.03 18:00")]
    frame, meta = load(tmp_path, rows)
    next_day = frame[frame.kickoff == pd.Timestamp("2026-08-02 18:00")]
    assert next_day.own_sample_count.tolist() == [1, 1]
    assert frame.iloc[-1].own_sample_count == 2
    assert meta["score_history_events"] == 3
