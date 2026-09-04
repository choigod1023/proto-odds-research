from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitcher_form import StarterForm, _App  # noqa: E402


def _boxscores():
    """홈 선발은 5경기 6이닝씩 꾸준, 원정 선발은 최근 난조."""
    rows = []
    for i in range(1, 6):
        day = f"2026-04-{i:02d}"
        rows.append({
            "date": day, "home_team": "H", "away_team": "A",
            "home_sp": {"pcode": "H1", "name": "홈에이스", "ip": 6.0,
                        "er": 2.0, "hr": 0.0, "bb": 1.0, "kk": 7.0},
            "away_sp": {"pcode": "A1", "name": "원정불펜데이", "ip": 4.0,
                        "er": 4.0, "hr": 1.0, "bb": 3.0, "kk": 2.0},
        })
    return rows


def test_xfip_is_walk_forward_and_excludes_the_current_game():
    form = StarterForm.from_boxscores(_boxscores())

    # 2026-04-03 시점에는 04-01·04-02 두 등판(12이닝)만 봐야 한다 → MIN_IP 15 미달.
    assert form.xfip("H1", "2026-04-03") is None
    # 04-05 시점에는 앞선 네 등판(24이닝)이 쌓여 값이 나온다.
    assert form.xfip("H1", "2026-04-05") is not None
    # 미래 등판을 끌어오지 않는다.
    assert form.xfip("H1", "2020-01-01") is None


def test_xfip_matches_the_documented_formula():
    form = StarterForm.from_boxscores(_boxscores())
    hx = form.xfip("H1", "2026-04-06")          # 앞선 다섯 등판 전부
    assert hx is not None

    ip, er, hr, bb, kk = 30.0, 10.0, 0.0, 5.0, 35.0
    fip_c, lg_hr9 = form._baseline("2026-04-06")
    w = ip / (ip + form.shrink_k)
    hr_adj = w * hr + (1 - w) * (lg_hr9 * ip / 9)
    expected = (13 * hr_adj + 3 * bb - 2 * kk) / ip + fip_c
    assert abs(hx - expected) < 1e-9


def test_matchup_delta_sign_favours_the_better_home_starter():
    form = StarterForm.from_boxscores(_boxscores())
    delta = form.matchup_delta("홈에이스", "원정불펜데이", "2026-04-06")
    assert delta is not None
    # 원정 선발이 더 나쁘므로 away_xfip > home_xfip → xfip_diff > 0.
    assert delta["away_xfip"] > delta["home_xfip"]
    assert delta["xfip_diff"] > 0


def test_missing_or_thin_starter_returns_no_matchup():
    form = StarterForm.from_boxscores(_boxscores())
    assert form.matchup_delta("홈에이스", "듣보투수", "2026-04-06") is None
    assert form.matchup_delta("홈에이스", "원정불펜데이", "2026-04-02") is None


def test_artifact_round_trip_preserves_xfip():
    form = StarterForm.from_boxscores(_boxscores())
    rebuilt = StarterForm(**form.to_artifact()["params"])
    for key, entry in form.to_artifact()["pitchers"].items():
        for row in entry["apps"]:
            day, ip, er, hr, bb, kk = row
            rebuilt.add_appearance(
                None if key.startswith("name:") else key, entry["name"],
                _App(str(day), float(ip), float(er), float(hr), float(bb), float(kk)))
    for k in rebuilt._apps:
        rebuilt._apps[k].sort(key=lambda a: a.date)
    rebuilt._starter_apps.sort(key=lambda a: a.date)

    assert rebuilt.xfip("H1", "2026-04-06") == form.xfip("H1", "2026-04-06")
