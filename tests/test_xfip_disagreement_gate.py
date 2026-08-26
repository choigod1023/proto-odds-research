import numpy as np
import pandas as pd

from pitcher_xfip import build_causal
from xfip_disagreement_gate import evaluate, merge_unique_games, select, wilson


def _pitcher(name, innings=6, er=2, hr=1, bb=1, kk=6, pcode=None):
    return {"name": name, "inn": innings, "er": er, "hr": hr, "bb": bb, "kk": kk,
            "pcode": pcode}


def _game(date, home_hr=1, away_hr=2):
    home = _pitcher("home starter", hr=home_hr)
    away = _pitcher("away starter", hr=away_hr)
    return {
        "date": pd.Timestamp(date), "home_team": "Home", "away_team": "Away",
        "home_sp": home, "away_sp": away,
        "home_all": [home], "away_all": [away],
    }


def test_rule_requires_market_disagreement_and_xfip_agreement():
    frame = pd.DataFrame({
        "p_market": [.45, .45, .45], "o_home": [2.3, 2.3, 2.3],
        "o_away": [1.6, 1.6, 1.6], "xfip_diff": [.4, -.4, .4], "y": [1., 0., 1.],
    })
    rule = {"market_max": .60, "edge_min": .01, "xfip_margin": .30, "ev_min": 0.}
    mask = select(frame, np.array([.55, .55, .40]), rule)
    assert mask.tolist() == [True, False, False]


def test_evaluation_uses_selected_side_odds_and_wilson_interval():
    frame = pd.DataFrame({"y": [1., 0.], "o_home": [2., 3.], "o_away": [2., 1.5]})
    result = evaluate(frame, np.array([.6, .6]), np.array([True, True]))
    assert result["accuracy"] == .5
    assert result["roi"] == 0.0
    lo, hi = wilson(1, 2)
    assert result["accuracy_wilson95"] == [lo, hi]


def test_causal_xfip_is_invariant_to_future_games():
    past = pd.DataFrame([
        _game("2024-04-01"), _game("2024-04-02"),
        _game("2024-04-03"), _game("2024-04-04"),
    ])
    future = pd.DataFrame([_game("2024-04-05", home_hr=20, away_hr=0)])

    replay_at_cutoff = build_causal(past)
    replay_with_future = build_causal(pd.concat([past, future], ignore_index=True))

    # 첫 경기에는 리그 prior가 없어서 닫히고, 15이닝 이후에는 계산된다.
    assert np.isnan(replay_at_cutoff.loc[0, "xfip_diff"])
    assert not np.isnan(replay_at_cutoff.loc[3, "xfip_diff"])
    pd.testing.assert_frame_equal(
        replay_at_cutoff.reset_index(drop=True),
        replay_with_future.iloc[:len(past)].reset_index(drop=True),
    )


def test_causal_xfip_does_not_share_history_between_same_name_player_ids():
    games = []
    for day in range(1, 4):
        home = _pitcher("동명이인", pcode="111")
        away = _pitcher("상대", pcode="900")
        games.append({
            "date": pd.Timestamp(f"2024-04-0{day}"),
            "home_team": "A팀", "away_team": "상대팀",
            "home_sp": home, "away_sp": away,
            "home_all": [home], "away_all": [away],
        })
    different = _pitcher("동명이인", pcode="222")
    away = _pitcher("상대", pcode="900")
    games.append({
        "date": pd.Timestamp("2024-04-04"),
        "home_team": "B팀", "away_team": "상대팀",
        "home_sp": different, "away_sp": away,
        "home_all": [different], "away_all": [away],
    })

    replay = build_causal(pd.DataFrame(games))

    assert np.isnan(replay.loc[3, "xfip_diff"])


def test_merge_excludes_all_ambiguous_duplicate_game_keys():
    keys = {
        "date": pd.to_datetime(["2024-04-01", "2024-04-01", "2024-04-02", "2024-04-03"]),
        "home_team": ["A", "A", "C", "E"],
        "away_team": ["B", "B", "D", "F"],
    }
    frame = pd.DataFrame({**keys, "y": [1, 0, 1, 0]})
    pitchers = pd.DataFrame({
        "date": pd.to_datetime(["2024-04-01", "2024-04-02", "2024-04-02", "2024-04-03"]),
        "home_team": ["A", "C", "C", "E"],
        "away_team": ["B", "D", "D", "F"],
        "xfip_diff": [.1, .2, .3, .4],
    })

    joined = merge_unique_games(frame, pitchers)

    assert joined[["home_team", "away_team"]].values.tolist() == [["E", "F"]]
    assert joined["xfip_diff"].tolist() == [.4]
