"""LLM 덧씌우기의 안전장치를 검증한다.

이 프로젝트의 결론은 "시장을 못 이긴다, 덜 잃을 뿐"이고 HANDOFF.md 는
"그 이상을 약속하면 거짓말이다" 라고 못박아 뒀다. LLM 은 근거 없이도 확신에 찬
문장을 잘 쓴다 — 이 제품이 팔지 않기로 한 바로 그것이다.

그래서 LLM 을 **믿지 않는 쪽으로** 설계했다. 여기서 검증하는 건 모델 품질이 아니라
**모델이 헛소리를 했을 때 그게 사이트로 나가지 않는가** 하나다.

    python -m pytest tests/test_commentary_llm.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import commentary_llm as L                 # noqa: E402

SRC = ("예상 픽은 볼티오리 승. 적중 확률 57% · 배당 1.55. "
       "LA에인절은 3연패 중이다. 최근 10경기 3승 7패, 평균 3.3득점이다.")


def test_원문에_없는_숫자를_지어내면_탈락():
    """제일 위험한 환각 — 그럴듯한 새 수치."""
    bad = SRC.replace("3연패", "7연패") + " 최근 원정 12경기 승률 24%다."
    ok, why = L._looks_safe(SRC, bad)
    assert not ok, "새 숫자가 통과했다"
    assert "숫자" in why


def test_숫자를_지우는_건_허용():
    """다듬기의 목적이 수치 덜어내기다. 이걸 막으면 기능 자체가 죽는다."""
    good = "볼티오리 쪽에 무게가 실린다. LA에인절은 3연패 중이라 흐름이 나쁘다."
    ok, why = L._looks_safe(SRC, good)
    assert ok, f"정상 결과가 탈락했다: {why}"


def test_길이_폭주는_탈락():
    """늘렸다는 건 대개 지어냈다는 뜻이다."""
    ok, _ = L._looks_safe(SRC, SRC + " 그리고 " * 80)
    assert not ok


def test_마크다운_이모지는_탈락():
    # ⚠️ 길이 검사(20자 미만)에 먼저 걸리면 형식 검사를 안 거친다.
    #    형식 검사를 정조준하려면 충분히 길어야 한다.
    md = "**볼티오리** 쪽에 무게가 실린다. LA에인절은 3연패 중이라 흐름이 나쁘다."
    ok, why = L._looks_safe(SRC, md)
    assert not ok and "형식" in why, f"마크다운이 통과했다: {why}"

    emoji = "볼티오리 쪽에 무게가 실린다 🔥. LA에인절은 3연패 중이라 흐름이 나쁘다."
    ok, why = L._looks_safe(SRC, emoji)
    assert not ok and "형식" in why, f"이모지가 통과했다: {why}"


def test_빈_응답은_탈락():
    ok, _ = L._looks_safe(SRC, "")
    assert not ok


def test_키가_없으면_원문을_그대로_돌려준다(monkeypatch):
    """API 키 없이도 파이프라인이 돌아야 한다 — 해설이 사라지면 안 된다."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert L.polish(SRC) == SRC


def test_호출이_실패해도_원문이_남는다(monkeypatch):
    """외부 API 장애가 PUBLISH 를 깨뜨리면 안 된다."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    def boom(*a, **k):
        raise RuntimeError("네트워크 끊김")

    monkeypatch.setattr(L, "_call", boom)
    L.polish._cache = {}
    assert L.polish(SRC) == SRC


def test_검사에_걸리면_원문이_남는다(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(L, "_call", lambda *a, **k: "볼티오리 12연승 중이라 확실하다.")
    L.polish._cache = {}
    assert L.polish(SRC) == SRC, "환각이 그대로 나갔다"


def test_같은_문장은_한_번만_부른다(monkeypatch):
    """캐시가 핵심이다 — 없으면 매시간 172경기를 다시 부른다."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    n = {"c": 0}

    def once(*a, **k):
        n["c"] += 1
        return "볼티오리 쪽에 무게가 실린다. LA에인절은 3연패 중이다."

    monkeypatch.setattr(L, "_call", once)
    L.polish._cache = {}
    a = L.polish(SRC)
    b = L.polish(SRC)
    assert a == b
    assert n["c"] == 1, f"같은 문장으로 {n['c']}번 호출했다 — 캐시가 안 먹는다"


def test_None_은_그대로():
    assert L.polish(None) is None
