from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commentary_llm import _looks_safe


def test_rejects_new_injury_claim_without_number():
    ok, why = _looks_safe("김투수가 선발 예고됐다.", "김투수가 선발 예고됐고 상대는 부상자가 있다.")
    assert not ok
    assert "민감 사실" in why


def test_rejects_dropped_player_entity():
    ok, why = _looks_safe(
        "선발 예고는 김투수와 이투수다.",
        "양 팀의 선발 예고가 경기 전에 정상적으로 확인된 상태다.",
        protected_terms=["김투수", "이투수"],
    )
    assert not ok
    assert "고유명사" in why


def test_accepts_style_only_rewrite():
    ok, why = _looks_safe(
        "선발 예고는 김투수와 이투수다. 강수확률은 20%다.",
        "김투수와 이투수가 선발로 예고됐다. 강수확률은 20%다.",
        protected_terms=["김투수", "이투수"],
    )
    assert ok, why
