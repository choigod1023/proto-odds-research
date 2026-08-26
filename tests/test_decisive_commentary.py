"""수치로 끝낸 선택이 해설에서 다시 흐려지지 않게 잠근다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from commentary import decision_summary  # noqa: E402


def test_언더오버_추천을_확정형으로_쓴다():
    out = decision_summary({
        "market": "언더오버",
        "label": "O 3.5",
        "line": 3.5,
        "선택": "오버",
        "모델확률": 0.5043,
        "시장확률": 0.5126,
        "배당": 1.74,
        "예상손익": -0.1225,
    }, "보되/글림트", "NEC", "sc")

    assert out.startswith("산출 시점의 최종 선택은 3.5골 오버다.")
    assert "모델확률은 50.4%" in out
    assert "시장확률은 51.3%" in out
    assert "기대수익은 -12.2%" in out
    assert "예상" not in out
    assert "가능성" not in out


def test_승패_추천은_실제_팀명을_쓴다():
    out = decision_summary({
        "market": "승패",
        "선택": "원정",
        "모델확률": 0.638,
        "시장확률": 0.571,
        "배당": 1.62,
    }, "FC안양", "인천유나", "sc")

    assert out.startswith("산출 시점의 최종 선택은 인천유나 승이다.")


def test_승일패_추천은_중복_라벨_대신_점수차로_쓴다():
    out = decision_summary({
        "market": "승①패",
        "label": "승①패",
        "선택": "원정2+",
        "모델확률": 0.4665,
    }, "FC안양", "인천유나", "sc")

    assert out.startswith("산출 시점의 최종 선택은 인천유나 2점 차 이상 승이다.")
    assert "승①패 승①패" not in out


def test_승오패_추천은_내부코드_대신_점수차로_쓴다():
    wide = decision_summary({
        "market": "승⑤패", "label": "승⑤패", "선택": "홈6+", "모델확률": 0.48,
    }, "SK", "LG", "bk")
    close = decision_summary({
        "market": "승⑤패", "label": "승⑤패", "선택": "5점차이내", "모델확률": 0.31,
    }, "SK", "LG", "bk")

    assert wide.startswith("산출 시점의 최종 선택은 SK 6점 차 이상 승이다.")
    assert close.startswith("산출 시점의 최종 선택은 5점 차 이내 승부다.")
    assert "승⑤패 승⑤패" not in wide + close


def test_핸디캡_추천은_기준과_선택팀을_함께_쓴다():
    out = decision_summary({
        "market": "핸디캡",
        "label": "H -2.0",
        "line": -2,
        "선택": "핸디원정",
        "모델확률": 0.7619,
    }, "한신", "주니치", "bs")

    assert out.startswith("산출 시점의 최종 선택은 홈팀 한신 -2.0 적용 후 주니치 쪽이다.")


def test_승패_배당이_없으면_가짜_반반_가격을_쓰지_않는다():
    from commentary import make_preview

    out = make_preview(
        "홈팀", "원정팀", "테스트리그", None, None, {},
        None, None, 0, 0, 88, 0, 0,
    )

    assert "승패 배당은 아직 발표되지 않았다" in out
    assert "동률로 가격했다" not in out
    assert "시장확률은 50%" not in out


def test_배구_언더오버는_세트가_아니라_총점을_쓴다():
    out = decision_summary({
        "market": "언더오버", "line": 182.5, "선택": "언더", "모델확률": 0.55,
    }, "현대캐피", "대한항공", "vl")
    assert out.startswith("산출 시점의 최종 선택은 182.5점 언더다.")


def test_전반전_마켓도_선택_범위를_문장에_남긴다():
    win = decision_summary({
        "market": "전반승무패", "선택": "전반원정", "모델확률": 0.44,
    }, "서울", "부산", "sc")
    total = decision_summary({
        "market": "전반언더오버", "line": 1.5, "선택": "전반언더", "모델확률": 0.58,
    }, "서울", "부산", "sc")

    assert win.startswith("산출 시점의 최종 선택은 부산 전반 승이다.")
    assert total.startswith("산출 시점의 최종 선택은 전반 1.5골 언더다.")


def test_추천이_없으면_결정문을_만들지_않는다():
    assert decision_summary(None, "FC안양", "인천유나", "sc") is None
