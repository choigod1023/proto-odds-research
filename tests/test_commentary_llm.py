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
DECISIVE_SRC = ("최종 모델 선택은 볼티오리 승이다. 모델확률은 57%, 배당은 1.55다. "
                "LA에인절은 3연패 중이다.")


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


def test_합니다체가_섞이면_탈락():
    out = "볼티오리 승을 선택합니다. LA에인절은 3연패 중입니다."
    ok, why = L._looks_safe(SRC, out)
    assert not ok and "문체 위반" in why


def test_확정_선택을_예상문으로_약화하면_탈락():
    out = "볼티오리 승리가 예상됩니다. 모델확률은 57%, 배당은 1.55다. LA에인절은 3연패 중이다."
    ok, why = L._looks_safe(DECISIVE_SRC, out)
    assert not ok and "완곡" in why


def test_실제_승패시장_문구를_예상문으로_약화하면_탈락():
    src = "승패 시장 1순위는 볼티오리 승이다. 시장확률은 57%, 배당은 1.55다."
    out = "볼티오리 승리가 예상된다. 시장확률은 57%, 배당은 1.55다."
    ok, why = L._looks_safe(src, out)
    assert not ok and "완곡" in why


def test_승패시장_1순위의_팀을_바꾸거나_지우면_탈락():
    src = ("승패 시장 1순위는 볼티오리 승이다. 시장확률은 57%, 배당은 1.55다. "
           "LA에인절은 3연패 중이다.")
    flipped = ("승패 시장 1순위는 LA에인절 승이다. 시장확률은 57%, 배당은 1.55다. "
               "LA에인절은 3연패 중이다.")
    missing = "시장은 팽팽하다. 시장확률은 57%, 배당은 1.55다. LA에인절은 3연패 중이다."

    for out in (flipped, missing):
        ok, why = L._looks_safe(src, out)
        assert not ok and "방향" in why


def test_동률_시장_템플릿의_1순위도_예상문으로_바꾸면_탈락():
    src = ("승패 시장은 두 팀을 사실상 동률로 가격했다. 1순위는 볼티오리 승이고 "
           "시장확률은 51%, 배당은 1.91이다.")
    out = ("시장은 팽팽하다. LA에인절 승리가 예상된다. "
           "시장확률은 51%, 배당은 1.91이다.")
    ok, why = L._looks_safe(src, out)
    assert not ok and "완곡" in why


def test_확정_선택을_결과_보장으로_강화해도_탈락():
    out = "볼티오리가 반드시 이긴다. 모델확률은 57%, 배당은 1.55다. LA에인절은 3연패 중이다."
    ok, why = L._looks_safe(DECISIVE_SRC, out)
    assert not ok and "결과 보장" in why


def test_근거_본문도_결과_보장으로_강화하면_탈락():
    src = "LA에인절은 3연패 중이다. 최근 득실 계산 결과는 언더다."
    out = "LA에인절은 3연패 중이다. 언더가 반드시 적중한다."
    ok, why = L._looks_safe(src, out)
    assert not ok and "결과 보장" in why


def test_선택과_확률을_분리한_단정문은_통과():
    out = "최종 모델 선택은 볼티오리 승이다. 모델확률은 57%, 배당은 1.55다. LA에인절은 3연패 중이다."
    ok, why = L._looks_safe(DECISIVE_SRC, out)
    assert ok, why


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


def test_하루_상한을_넘으면_더_안_부른다(monkeypatch):
    """비용 천장. 이게 없으면 주기 상한만으로 24×120 = 2,880건/일까지 열린다."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(L, "MAX_CALLS_DAY", 2)
    n = {"c": 0}

    def counted(text, *a, **k):
        n["c"] += 1
        return "볼티오리 쪽에 무게가 실린다. LA에인절은 3연패 중이라 흐름이 나쁘다."

    monkeypatch.setattr(L, "_call", counted)
    L.polish._cache = {}
    L.polish._budget = {"date": L._today(), "used": 0}
    L._calls = 0

    # 캐시를 타지 않도록 매번 다른 문장을 준다
    outs = [L.polish(SRC + f" 표본 {i}번째 문장이다.") for i in range(5)]
    assert n["c"] == 2, f"상한 2건인데 {n['c']}번 불렀다"
    # 상한 뒤의 것들은 원문이 그대로 남아야 한다 — 해설이 사라지면 안 된다
    assert outs[-1].startswith("예상 픽은"), "상한 초과분이 비었다"


def test_예산은_날짜가_바뀌면_초기화된다(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "BUDGET_PATH", tmp_path / "budget.json")
    L._budget_save({"date": "2020-01-01", "used": 999})
    b = L._budget_load()
    assert b["used"] == 0 and b["date"] == L._today()


def test_캐시_적중은_예산을_안_쓴다(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(L, "_call", lambda *a, **k: "볼티오리 쪽에 무게가 실린다. 흐름이 나쁘다.")
    L.polish._cache = {}
    L.polish._budget = {"date": L._today(), "used": 0}
    L._calls = 0
    L.polish(SRC)
    used_after_first = L.polish._budget["used"]
    for _ in range(10):
        L.polish(SRC)
    assert L.polish._budget["used"] == used_after_first, "캐시 적중이 예산을 깎았다"
