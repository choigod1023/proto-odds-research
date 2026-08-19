"""실제 기준선과 교차 마켓 충돌이 '의외성' 해설로 보존되는지 검증한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from commentary import _market_tension, make_preview  # noqa: E402
from generate_v2 import _market_context      # noqa: E402
from team_form import Form                   # noqa: E402


def _form(team: str, scored: float, conceded: float, results: str,
          *, home_w: int, home_l: int, away_w: int, away_l: int) -> Form:
    recent = [{"gf": scored, "ga": conceded} for _ in range(3)]
    return Form(
        team=team, league="MLB", w=results.count("W"), l=results.count("L"),
        last10=list(results), recent_games=recent,
        scored=[scored] * 10, conceded=[conceded] * 10,
        home_w=home_w, home_l=home_l, away_w=away_w, away_l=away_l,
        close_games=6, margin_prev=0.0, margin_recent=0.0,
    )


def _options() -> list[dict]:
    return [
        {"market": "핸디캡", "label": "H +2.5", "line": 2.5,
         "선택": "핸디홈", "시장확률": 0.5853, "모델확률": 0.7642},
        {"market": "핸디캡", "label": "H +2.5", "line": 2.5,
         "선택": "핸디원정", "시장확률": 0.4147, "모델확률": 0.2358},
        {"market": "승①패", "label": "승①패", "line": None,
         "선택": "1점차", "시장확률": 0.2313, "모델확률": 0.3608},
        {"market": "언더오버", "label": "U 11.5", "line": 11.5,
         "선택": "언더", "시장확률": 0.5129, "모델확률": 0.6357},
        {"market": "언더오버", "label": "O 11.5", "line": 11.5,
         "선택": "오버", "시장확률": 0.4871, "모델확률": 0.3643},
    ]


def _preview() -> str:
    rockies = _form("콜로로키", 4.7, 6.6, "WWWLLLLLLL",
                    home_w=21, home_l=27, away_w=15, away_l=33)
    dodgers = _form("LA다저스", 4.9, 4.1, "WWWWWWWLLL",
                    home_w=35, home_l=15, away_w=34, away_l=17)
    return make_preview(
        "콜로로키", "LA다저스", "MLB", rockies, dodgers, {},
        p_model=0.4723, p_market=0.3486,
        odds_home=2.40, odds_away=1.39, payout=0.88,
        ev_home=0.0, ev_away=0.0, sport="bs", limit=2000,
        market_context=_market_context(_options()),
    )


def test_실제_기준선으로_언더오버를_판단한다():
    out = _preview()
    assert "실제 기준선 11.5점" in out
    assert "언더 쪽" in out
    assert "기준선 8.5" not in out


def test_시장확률을_자체_적중확률이라_부르지_않는다():
    out = _preview()
    assert "시장 기본값은 LA다저스 승 65%" in out
    assert "적중 확률은 65%" not in out


def test_교차_마켓_충돌과_역배_경로를_설명한다():
    out = _preview()
    assert "어라 포인트" in out
    assert "검증 전 득점 모델은 53%" in out
    assert "콜로로키 쪽 핸디캡(H +2.5)" in out
    assert "1점차 가능성" in out
    assert "접전이 길어져" in out
    assert "쏠림 의심" in out


def test_평균득점차를_다득점차_확률로_오해하지_않는다():
    out = _preview()
    assert "2점 차 이상" not in out
    assert "2점차 이상" not in out


def test_시장문맥을_선택지에서_정확히_뽑는다():
    context = _market_context(_options())
    assert context["total"]["line"] == 11.5
    assert context["handicap"]["home_model"] == 0.7642
    assert context["margin_band"]["close_market"] == 0.2313


def test_승패_모델_괴리_하나만으로_역배를_암시하지_않는다():
    out = _market_tension(
        "콜로로키", "LA다저스", p_model=0.4723, p_market=0.3486,
        context={"total": {"line": 11.5}},
    )
    assert out is None
