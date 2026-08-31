"""외국어 선수명을 한글 표기로 캐시한다.

선수 이름은 일반 문장 번역이 아니라 고유명사 음역이다. 새 이름만 Gemini에 묶어서
요청하고, 검증된 결과는 영구 캐시에 남긴다. 키나 네트워크가 없으면 원문을 그대로
유지하므로 선수 자료 생성 자체가 실패하지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

try:  # 패키지 테스트와 src/ 직접 실행을 모두 지원한다.
    from .runtime_db import RuntimeDatabase, database_enabled, persist_document
except ImportError:  # pragma: no cover - generate_v2.py의 직접 실행 경로
    from runtime_db import RuntimeDatabase, database_enabled, persist_document

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "raw" / "llm_cache" / "player_names_ko.json"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
BATCH_SIZE = 60
MAX_CALLS = int(os.environ.get("NAME_LOCALIZER_MAX_CALLS", "20"))
TIMEOUT = 30
_HANGUL = re.compile(r"[가-힣]")
_FOREIGN = re.compile(r"[A-Za-zぁ-んァ-ヶ一-龯]")


def _load() -> dict[str, str]:
    try:
        stored = (RuntimeDatabase().get_document("player_names_ko")
                  if database_enabled() else None)
        value = stored if stored is not None else json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in value.items() if _valid(str(k), str(v))}
    except (OSError, ValueError, TypeError):
        return {}


def _save(cache: dict[str, str]) -> None:
    try:
        persist_document("player_names_ko", cache, CACHE_PATH, indent=2)
    except OSError as exc:
        print(f"  [선수명] 캐시 저장 실패(무시): {exc}")


def _valid(native: str, korean: str) -> bool:
    return bool(native.strip() and _HANGUL.search(korean) and 1 < len(korean.strip()) <= 80)


def _player_rows(value) -> list[dict]:
    rows: list[dict] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_player_rows(item))
    elif isinstance(value, dict):
        name = value.get("name")
        # 자료원/모델 메타데이터의 name 필드는 제외하고 선수 식별 정보가 있는 행만 잡는다.
        if (isinstance(name, str) and name.strip()
                and any(key in value for key in ("player_id", "player_code", "position", "stats"))):
            rows.append(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                rows.extend(_player_rows(child))
    return rows


def _call(names: list[str], api_key: str) -> dict[str, str]:
    system = (
        "스포츠 선수 이름의 한국어 표기 편집자다. 입력 JSON 배열의 각 고유명사를 "
        "한국 언론에서 읽기 쉬운 한글 음역으로 바꿔 JSON 객체만 반환하라. "
        "사람을 바꾸거나 번역 설명을 덧붙이지 말고, 모든 원문을 정확히 key로 보존하라."
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(names, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }
    response = requests.post(f"{ENDPOINT}/{MODEL}:generateContent?key={api_key}",
                             json=body, timeout=TIMEOUT)
    response.raise_for_status()
    parts = (response.json().get("candidates") or [{}])[0].get("content", {}).get("parts") or [{}]
    parsed = json.loads(parts[0].get("text") or "{}")
    if not isinstance(parsed, dict):
        return {}
    allowed = set(names)
    return {native: str(korean).strip() for native, korean in parsed.items()
            if native in allowed and _valid(native, str(korean))}


def localize_player_names(records: list[dict], *, api_key: str | None = None,
                          cache: dict[str, str] | None = None) -> dict:
    """records 내부 선수명을 한글 우선 구조로 바꾸고 처리 통계를 반환한다."""
    store = cache if cache is not None else _load()
    rows = _player_rows(records)
    pending: list[str] = []
    seen: set[str] = set()
    for row in rows:
        current = str(row.get("name") or "").strip()
        native = str(row.get("native_name") or row.get("name") or "").strip()
        supplied = str(row.get("name_ko") or "").strip()
        if supplied and _valid(native, supplied):
            store[native] = supplied
        elif native != current and _HANGUL.search(current):
            store[native] = current
        if native and not _HANGUL.search(native) and _FOREIGN.search(native) and native not in store and native not in seen:
            pending.append(native)
            seen.add(native)

    key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
    calls = failures = 0
    if key:
        for offset in range(0, min(len(pending), BATCH_SIZE * MAX_CALLS), BATCH_SIZE):
            if failures >= 3:
                break
            batch = pending[offset:offset + BATCH_SIZE]
            try:
                store.update(_call(batch, key))
                calls += 1
            except Exception as exc:  # noqa: BLE001 — 원문 유지가 정상 폴백이다
                failures += 1
                if failures <= 3:
                    print(f"  [선수명] 한글화 실패(원문 유지): {type(exc).__name__}: {exc}")

    localized = 0
    for row in rows:
        current = str(row.get("name") or "").strip()
        native = str(row.get("native_name") or current).strip()
        korean = str(row.get("name_ko") or store.get(native) or store.get(current) or "").strip()
        if korean and _valid(native or current, korean):
            if current != korean:
                row.setdefault("native_name", current)
            row["name_ko"] = korean
            row["name"] = korean
            localized += 1

    if cache is None and (calls or localized):
        _save(store)
    unresolved = sum(1 for native in pending if native not in store)
    return {"players": len(rows), "localized": localized, "pending": unresolved,
            "calls": calls, "failures": failures, "cache_size": len(store), "at": int(time.time())}
