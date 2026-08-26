"""템플릿 해설을 LLM 이 사람 말투로 고쳐 쓴다 — **사실은 건드리지 않고**.

왜 덧씌우기인가 (새로 쓰게 하지 않는 이유)
------------------------------------------
이 프로젝트의 결론은 "우리는 시장을 못 이긴다, 덜 잃을 뿐"이다. HANDOFF.md 가
**"그 이상을 약속하면 거짓말이다"** 라고 못박아 뒀다. 그런데 LLM 은 근거가 없어도
그럴듯하고 확신에 찬 문장을 잘 쓴다 — 이 제품이 팔지 않기로 한 바로 그것이다.

그래서 LLM 에게 **자료를 주고 쓰게 하지 않는다.** 이미 완성된 템플릿 문장을 주고
**말투만 고치라**고 시킨다. 새 사실을 넣을 자료 자체를 주지 않으므로, 환각이
들어올 통로가 구조적으로 막힌다. 그리고 결과를 다시 검사한다(_looks_safe).

왜 캐시가 먼저인가
------------------
generate_v2 는 매 주기 전 경기를 다시 만든다. live 만 172경기이고 갱신은 매시간이라,
캐시 없이 붙이면 하루 4,000회를 부른다. 그런데 경기와 배당이 그대로면 템플릿 문장도
그대로다 — **템플릿 문장 자체를 캐시 키로 쓴다.** 문장이 안 바뀌었으면 다시 부를
이유가 없다. 실제 호출은 새 경기·배당이 움직인 경기로만 줄어든다.

없어도 도는가
-------------
돈다. 키가 없거나·실패하거나·검사에 걸리면 **템플릿 문장을 그대로 돌려준다.**
해설이 사라지는 일은 없다. 외부 API 를 PUBLISH 경로에 넣으면서 장애 표면을
늘리지 않으려는 것이다(2026-08-06 에 볼륨 하나로 39시간 멈춘 전례가 있다).

환경변수
--------
  GEMINI_API_KEY     없으면 덧씌우기 자체를 건너뛴다(조용히, 템플릿 그대로)
  GEMINI_MODEL       기본 gemini-3.1-flash-lite
  LLM_MAX_CALLS      한 주기 최대 호출 수(기본 120) — 한 번에 몰리는 걸 막는다
  LLM_MAX_CALLS_DAY  하루 최대 호출 수(기본 700) — **비용 상한은 이쪽이다**

비용 상한
---------
실측 1건 = 입력 738 · 출력 298 토큰 ≈ $0.00063 (약 0.9원, gemini-3.1-flash-lite).
주기 상한만 두면 24주기 × 120 = 2,880건/일까지 열려 있어 월 7만원을 넘길 수 있다.
그래서 **하루 총량**을 막는다: 700건/일 × 30일 × 0.9원 ≈ 월 18,900원이 천장이다.
한도를 넘긴 날은 그대로 템플릿으로 돈다 — 해설이 사라지지는 않는다.

⚠️ generate_v2 는 주기마다 새 프로세스로 뜬다. 카운터를 메모리에 두면 매번
   0 으로 시작해 상한이 아무것도 막지 못한다. 반드시 디스크에 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "raw" / "llm_cache" / "commentary.json"
BUDGET_PATH = ROOT / "data" / "raw" / "llm_cache" / "budget.json"

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
STYLE_VERSION = "korean-decision-v3"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 25
MAX_CALLS = int(os.environ.get("LLM_MAX_CALLS", "120"))
# 하루 700건 × 30일 × 0.9원 ≈ 월 18,900원. 이게 비용 천장이다.
MAX_CALLS_DAY = int(os.environ.get("LLM_MAX_CALLS_DAY", "700"))
CACHE_MAX = 4000               # 엔트리 상한 — data/ 는 30분마다 커밋되므로 무한정 키우면 안 된다

_calls = 0
_hits = 0
_fails = 0
_skipped_budget = 0


def _today() -> str:
    """머신 TZ 는 Asia/Seoul 이다. 한국 날짜로 하루를 끊는다."""
    return time.strftime("%Y-%m-%d", time.localtime())


def _budget_load() -> dict:
    """오늘 쓴 호출 수. 날짜가 바뀌면 0 부터 다시 센다."""
    try:
        b = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
        if b.get("date") == _today():
            return b
    except (OSError, ValueError):
        pass
    return {"date": _today(), "used": 0}


def _budget_save(b: dict) -> None:
    try:
        BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        BUDGET_PATH.write_text(json.dumps(b), encoding="utf-8")
    except OSError as e:
        print(f"  [llm] 예산 저장 실패(무시): {e}")


SYSTEM = """너는 스포츠 프리뷰 문장을 다듬는 편집자다. 기자가 아니다.

주어진 문장은 **이미 사실 확인이 끝난 완성문**이다. 네 일은 딱 하나 — 기계가 쓴
티가 나는 문장을 사람이 쓴 것처럼 자연스럽게 고치는 것이다.

절대 규칙(하나라도 어기면 결과물은 버려진다):
- 원문에 없는 사실을 **절대** 추가하지 마라. 팀 이름, 선수, 부상, 날씨, 순위,
  일정 — 원문에 없으면 쓰지 마라. 너는 이 경기에 대해 아무것도 모른다.
- 숫자를 바꾸지 마라. 지우는 건 되지만 새로 만들거나 반올림하지 마라.
- 승패 판단을 뒤집지 마라. 원문이 A 우세라고 하면 A 우세다.
- **결정과 결과를 구분하라.** 원문이 "승패 시장 1순위", "시장 수치 1위", "수치 판정",
  "최종 모델 선택"으로 결론을 냈다면 `예상한다`, `가능성이 있다`, `~로 보인다`로
  물러서지 마라. 계산 결과와 선택은 단정형으로 보존한다.
- 경기 결과를 보장하지 마라. "최종 선택은 A다"는 유지하지만 이를 "A가 반드시
  이긴다", "확실하다"로 바꾸지 마라. 확률의 불확실성은 원문의 숫자로 표현한다.
- 분석 문체는 `한다체`로만 쓴다. `합니다`, `됩니다`, `습니다`, `입니다` 같은
  존댓말 종결을 섞지 마라.
- 이모지·마크다운·머리말·인용부호를 붙이지 마라. 고친 본문만 출력해라.
- "승패 시장 1순위"와 "시장 수치 1위"는 배당에서 읽은 시장 확률이지 이 서비스의 적중 확률이 아니다.
  이를 "예상", "추천", "적중 확률"로 바꾸지 마라.
- "어라 포인트"와 "모델이 계산한 역배 경로"의 의미를 보존하라. 역배가 유력하다고
  단정하지 마라.
- "쏠림 의심"을 실제 쏠림이나 투표량이 확인된 것처럼 고치지 마라.

고칠 것:
- 같은 구조가 반복되면 문장을 합치거나 순서를 바꿔라.
- 숫자가 연달아 나오면 흐름을 먼저 말하고 숫자를 뒤로 보내라.
- "~이다"가 반복되면 문장 구조와 서술 동사를 바꾸되 `한다체`는 유지한다.
- 길이는 원문과 비슷하게. 늘리지 마라."""


def _load() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache: dict) -> None:
    # 오래된 것부터 버린다. 값에 저장한 ts 로 정렬한다.
    if len(cache) > CACHE_MAX:
        keep = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0),
                      reverse=True)[:CACHE_MAX]
        cache = dict(keep)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False),
                              encoding="utf-8")
    except OSError as e:
        print(f"  [llm] 캐시 저장 실패(무시): {e}")


def _key(text: str) -> str:
    """템플릿 문장 + 모델 + 문체 계약 버전이 키다."""
    return hashlib.sha256(f"{MODEL}\n{STYLE_VERSION}\n{text}".encode()).hexdigest()[:24]


_NUM = re.compile(r"\d+(?:\.\d+)?")
_DECISIVE = re.compile(
    r"(?:승패 시장 1순위|1순위는|시장 수치 1위|수치 판정|최종 (?:모델|수치) 선택|"
    r"추천하지 않는다|추천에서 제외한다)"
)
_SOFTENED = re.compile(r"(?:예상(?:한다|된다|했다|합니다|됩니다|했습니다)|"
                       r"전망(?:한다|된다|합니다|됩니다)|가능성이 (?:있다|높다|있습니다|높습니다)|"
                       r"(?:것|듯)으로 보(?:인다|입니다)|것 같(?:다|습니다)|듯하(?:다|습니다))")
_OVERCONFIDENT = re.compile(r"(?:반드시|확실(?:하다|합니다|한)|100%)")
_HONORIFIC_ENDING = re.compile(
    r"(?:합니다|됩니다|습니다|입니다|했습니다|였습니다|겠습니다|하세요)(?:[.!?]|$)"
)
_MARKET_FIRST_PATTERNS = (
    re.compile(r"승패 시장 1순위는\s*(?P<choice>[^.]+?)\s*승(?:이다|이다가|이고|[.!?,]|$)"),
    re.compile(r"1순위는\s*(?P<choice>[^.]+?)\s*승(?:이다|이다가|이고|[.!?,]|$)"),
    re.compile(r"시장은\s*(?P<choice>[^.]+?)\s*승을\s*1순위(?:로|에)"),
    re.compile(r"(?P<choice>[^.]+?)\s*승이\s*승패 시장 1순위(?:다|이다|로)"),
)


def _market_first_choices(text: str) -> set[str]:
    """승패 시장 1순위의 팀을 뽑는다. 방향이 바뀌거나 사라지면 원문을 쓴다."""
    choices = set()
    for pattern in _MARKET_FIRST_PATTERNS:
        for match in pattern.finditer(text):
            choice = re.sub(r"\s+", " ", match.group("choice")).strip()
            if choice:
                choices.add(choice)
    return choices


def _looks_safe(src: str, out: str) -> tuple[bool, str]:
    """고쳐 쓴 결과가 원문의 사실을 지켰는지 검사한다.

    LLM 을 믿지 않는다. 통과 못 하면 템플릿 원문을 쓴다.
    """
    if not out or len(out) < 20:
        return False, "너무 짧다"
    # 길이 폭주 — 늘렸다는 건 뭔가 지어냈다는 뜻일 때가 많다
    if len(out) > len(src) * 1.35 + 40:
        return False, f"너무 길다({len(src)}→{len(out)})"
    # 새 숫자가 생기면 안 된다. 지우는 건 허용.
    new_nums = set(_NUM.findall(out)) - set(_NUM.findall(src))
    if new_nums:
        return False, f"원문에 없는 숫자 {sorted(new_nums)[:4]}"
    # 마크다운·이모지가 섞이면 형식 지시를 무시한 것이다
    if re.search(r"[*#`]|^\s*[-•]", out) or re.search(r"[\U0001F300-\U0001FAFF]", out):
        return False, "형식 위반(마크다운·이모지)"
    # 수치로 확정한 결정을 LLM 이 다시 완곡한 예상문으로 바꾸면 원문의 의미가
    # 달라진다. 결과 보장은 막되, 결정 자체는 흐리지 않는다.
    if _DECISIVE.search(src) and _SOFTENED.search(out):
        return False, "수치 판정을 완곡한 예상문으로 바꿨다"
    source_market_first = _market_first_choices(src)
    if source_market_first:
        output_market_first = _market_first_choices(out)
        if output_market_first != source_market_first:
            return False, "승패 시장 1순위의 방향을 바꾸거나 삭제했다"
    if _OVERCONFIDENT.search(out) and not _OVERCONFIDENT.search(src):
        return False, "수치 선택을 경기 결과 보장으로 바꿨다"
    if _HONORIFIC_ENDING.search(out):
        return False, "문체 위반(한다체가 아닌 존댓말 종결)"
    return True, ""


def _call(text: str, api_key: str) -> str | None:
    url = f"{ENDPOINT}/{MODEL}:generateContent?key={api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700},
    }
    r = requests.post(url, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:160]}")
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or [{}]
    return (parts[0].get("text") or "").strip() or None


def polish(text: str | None) -> str | None:
    """템플릿 해설 한 건을 다듬는다. 무슨 일이 있어도 원문 이상은 잃지 않는다."""
    global _calls, _hits, _fails, _skipped_budget
    if not text:
        return text

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return text                       # 키가 없으면 조용히 템플릿 그대로

    cache = polish._cache
    k = _key(text)
    hit = cache.get(k)
    if hit:
        _hits += 1
        return hit["out"]                 # 캐시 적중은 예산을 안 쓴다

    if _calls >= MAX_CALLS:
        return text                       # 이번 주기 상한 — 나머지는 다음 주기에

    # 하루 총량 상한. 넘긴 날은 남은 경기를 템플릿으로 낸다.
    # 여기서 막지 않으면 주기 상한만으로는 24×120 = 2,880건/일까지 열린다.
    if polish._budget["used"] >= MAX_CALLS_DAY:
        _skipped_budget += 1
        return text

    try:
        _calls += 1
        polish._budget["used"] += 1
        out = _call(text, api_key)
    except Exception as e:                # noqa: BLE001
        _fails += 1
        if _fails <= 3:
            print(f"  [llm] 호출 실패(템플릿 사용): {type(e).__name__} {e}")
        return text

    if not out:
        _fails += 1
        return text

    ok, why = _looks_safe(text, out)
    if not ok:
        _fails += 1
        if _fails <= 5:
            print(f"  [llm] 검사 탈락(템플릿 사용): {why}")
        return text

    cache[k] = {"out": out, "ts": int(time.time())}
    return out


polish._cache = _load()                   # 프로세스 시작 시 한 번만 읽는다
polish._budget = _budget_load()


def flush(verbose: bool = True) -> None:
    """캐시·예산을 저장하고 이번 주기 요약을 찍는다. 생성기 끝에서 부른다."""
    _save(polish._cache)
    _budget_save(polish._budget)
    if verbose:
        used = polish._budget["used"]
        won = used * 0.9              # 1건 ≈ 0.9원 (실측 738/298 토큰)
        msg = (f"해설 다듬기 — 호출 {_calls}건 · 캐시적중 {_hits}건 · "
               f"실패/탈락 {_fails}건 · 캐시 {len(polish._cache)}개 (model={MODEL})")
        if _skipped_budget:
            msg += f"\n  ⚠️ 하루 상한({MAX_CALLS_DAY}건) 도달 — {_skipped_budget}건은 템플릿으로 냈다"
        msg += f"\n  오늘 누적 {used}/{MAX_CALLS_DAY}건 ≈ {won:,.0f}원"
        print(msg)
