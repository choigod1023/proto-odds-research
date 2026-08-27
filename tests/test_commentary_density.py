"""해설이 수치 나열로 되돌아가지 않게 잠근다.

왜 이 테스트가 있나
-------------------
2026-08-08 실측: 해설 225건에서 **1건당 숫자가 평균 45.2개**(483자에 45개 —
열 자에 하나 꼴)였고, `최근 3경기에서 10-9, 0-14, 8-5` 형태의 원시 스코어 나열이
**97%** 에 들어 있었다. 스코어 세 개를 읽고 알 수 있는 건 없다 — 읽는 사람이
머릿속에서 다시 요약해야 한다.

그리고 그 수치 문장들이 자리를 다 먹어 **27% 가 길이 상한에서 잘렸고**,
제일 먼저 희생된 게 결론을 뒤집을 수도 있는 `반대 근거: ~` 문장이었다(28% 만 생존).

여기서 막지 않으면 "숫자를 하나만 더" 가 쌓여 조용히 원래대로 돌아간다.

    python -m pytest tests/test_commentary_density.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from commentary import make_preview          # noqa: E402
from team_form import Form                   # noqa: E402


def _form(team: str, *, scored, conceded, last10, streak_kind="", streak_n=0,
          recent, **kw) -> Form:
    return Form(
        team=team, league="MLB",
        last10=list(last10), streak_kind=streak_kind, streak_n=streak_n,
        recent_games=list(recent),
        scored=list(scored), conceded=list(conceded),
        home_w=28, home_l=23, away_w=16, away_l=34,
        margin_recent=-0.4, margin_prev=0.6,
        **kw,
    )


def _sample() -> str:
    """실제 산출물에서 본 것과 같은 모양의 경기 하나."""
    fh = _form(
        "볼티오리",
        recent=[{"gf": 10, "ga": 9}, {"gf": 0, "ga": 14}, {"gf": 8, "ga": 5}],
        scored=[10, 0, 8, 4, 3, 6, 2, 5, 7, 1],
        conceded=[9, 14, 5, 3, 6, 2, 8, 4, 5, 5],
        last10=list("WLWLWLWLWL"),
    )
    fa = _form(
        "LA에인절",
        recent=[{"gf": 4, "ga": 7}, {"gf": 2, "ga": 3}, {"gf": 4, "ga": 6}],
        scored=[4, 2, 4, 3, 2, 5, 1, 4, 3, 5],
        conceded=[7, 3, 6, 4, 5, 2, 6, 3, 4, 1],
        last10=list("LLLWLWLWLL"), streak_kind="연패", streak_n=3,
    )
    return make_preview(
        "볼티오리", "LA에인절", "MLB", fh, fa, {},
        p_model=0.59, p_market=0.57, odds_home=1.55, odds_away=2.45,
        payout=0.88, ev_home=-0.11, ev_away=-0.09, sport="bs",
    )


def test_원시_스코어_나열이_없다():
    """`최근 3경기에서 10-9, 0-14, 8-5` 같은 나열이 다시 들어오면 실패한다."""
    out = _sample()
    assert not re.search(r"\d+-\d+, *\d+-\d+", out), (
        f"원시 스코어 나열이 되살아났다:\n{out}"
    )


def test_숫자_밀도가_과하지_않다():
    """숫자 밀도 상한.

    개선 전 이 표본은 숫자 45개 언저리였고 지금은 29개다.
    상한은 32. 여유는 주되 예전 수준으로는 못 돌아가게 잠근다.
    """
    out = _sample()
    n = len(re.findall(r"\d+(?:\.\d+)?", out))
    assert n <= 32, f"숫자가 {n}개다(상한 32). 개선 전 45개 수준으로 돌아가는 중:\n{out}"


def test_반론이_결론_가까이_온다():
    """'반대 근거: ~' 는 잘려선 안 되는 문장이라 앞쪽에 있어야 한다."""
    out = _sample()
    if "반대 근거:" not in out:
        return                      # 이 표본에 반론 조건이 안 걸린 경우
    assert out.index("반대 근거:") < len(out) * 0.5, (
        f"'반대 근거:' 문장이 뒤쪽에 있어 길이 상한에서 잘릴 자리다:\n{out}"
    )


def test_문장이_끊기지_않는다():
    out = _sample()
    assert not out.rstrip().endswith(","), f"문장이 쉼표에서 끊겼다:\n{out}"


if __name__ == "__main__":
    text = _sample()
    print(text)
    print("\n---")
    print(f"길이 {len(text)}자 · 숫자 {len(re.findall(r'[0-9]+(?:[.][0-9]+)?', text))}개")
