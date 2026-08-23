"""동행복권 공식 JSON API → 검증 가능한 로또 6/45 원장 수집기."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import time
from typing import Any

import requests

from lotto645 import DrawRecord, KST, estimated_latest_draw, validate_draws


OFFICIAL_ENDPOINT = "https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do"
OFFICIAL_RESULT_PAGE = "https://www.dhlottery.co.kr/lt645/result"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": OFFICIAL_RESULT_PAGE,
    "User-Agent": "proto-odds-research/lotto-audit (+https://github.com/choigod1023/proto-odds-research)",
}


def _parse_date(value: Any) -> str:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"공식 추첨일 형식이 아닙니다: {value!r}")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def parse_official_item(item: dict[str, Any], *, source_url: str, collected_at: str) -> DrawRecord:
    """2026 개편 API 필드를 내부 원장으로 옮긴다. tm1~6은 실제 순서가 아닌 정렬번호다."""
    numbers = tuple(int(item[f"tm{i}WnNo"]) for i in range(1, 7))
    item_hash = hashlib.sha256(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DrawRecord(
        draw_no=int(item["ltEpsd"]),
        draw_date=_parse_date(item["ltRflYmd"]),
        numbers_sorted=tuple(sorted(numbers)),
        bonus_number=int(item["bnsWnNo"]),
        numbers_draw_order=None,
        sales_amount=int(item["wholEpsdSumNtslAmt"]) if item.get("wholEpsdSumNtslAmt") is not None else None,
        winner_count_by_rank={
            str(rank): int(item[f"rnk{rank}WnNope"])
            for rank in range(1, 6) if item.get(f"rnk{rank}WnNope") is not None
        },
        prize_by_rank={
            str(rank): int(item[f"rnk{rank}WnAmt"])
            for rank in range(1, 6) if item.get(f"rnk{rank}WnAmt") is not None
        },
        source_url=source_url,
        collected_at=collected_at,
        raw_data_hash=item_hash,
        verification_status={
            "draw_no": "official_api",
            "draw_date": "official_api",
            "numbers_sorted": "official_api",
            "bonus_number": "official_api",
            "sales_amount": "official_api",
            "winner_count_by_rank": "official_api",
            "numbers_draw_order": "unavailable_not_inferred",
            "machine_id": "unavailable_not_inferred",
            "ball_set_id": "unavailable_not_inferred",
            "location": "unavailable_not_inferred",
            "draw_time": "unavailable_not_inferred",
            "procedure_version": "unavailable_not_inferred",
        },
    )


def fetch_official_batch(center_draw: int, *, timeout: float = 35.0, retries: int = 3) -> list[DrawRecord]:
    params = {"srchDir": "center", "srchLtEpsd": int(center_draw)}
    source_url = f"{OFFICIAL_ENDPOINT}?srchDir=center&srchLtEpsd={int(center_draw)}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(OFFICIAL_ENDPOINT, params=params, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("data", {}).get("list", [])
            collected_at = datetime.now(KST).isoformat(timespec="seconds")
            return [parse_official_item(item, source_url=source_url, collected_at=collected_at) for item in items]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.8 * (2 ** attempt))
    raise RuntimeError(f"공식 API 수집 실패(center={center_draw}): {last_error}") from last_error


def resolve_latest_draw(candidate: int | None = None) -> int:
    """달력 후보를 API로 확인한다. 발표 지연에도 미래 회차를 원장에 만들지 않는다."""
    candidate = candidate or estimated_latest_draw()
    for center in range(candidate, max(0, candidate - 5), -1):
        rows = fetch_official_batch(center)
        available = [row.draw_no for row in rows if row.draw_no <= candidate]
        if available:
            return max(available)
    raise RuntimeError("최근 공식 회차를 확인하지 못했습니다")


def collect_official_draws(
    *, first_draw: int = 1, last_draw: int | None = None, workers: int = 3,
) -> list[DrawRecord]:
    if first_draw < 1:
        raise ValueError("first_draw는 1 이상이어야 합니다")
    latest = resolve_latest_draw(last_draw)
    if latest < first_draw:
        raise ValueError("last_draw가 first_draw보다 작습니다")

    # 이 API는 요청 회차와 직전 9개를 돌려준다.
    centers = list(range(latest, first_draw - 1, -10))
    # API는 과거 구간에서 요청 회차를 가운데 둔 창을 반환한다. 시작 경계 자체를
    # 한 번 더 요청해야 1·2회처럼 창 왼쪽 끝의 회차가 빠지지 않는다.
    if centers[-1] != first_draw:
        centers.append(first_draw)
    records: dict[int, DrawRecord] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as executor:
        futures = {executor.submit(fetch_official_batch, center): center for center in centers}
        for future in as_completed(futures):
            for record in future.result():
                if first_draw <= record.draw_no <= latest:
                    records[record.draw_no] = record

    ordered = validate_draws(list(records.values()), require_contiguous=True)
    expected = latest - first_draw + 1
    if len(ordered) != expected:
        missing = sorted(set(range(first_draw, latest + 1)) - set(records))
        raise RuntimeError(f"공식 원장 {expected - len(ordered)}회 누락: {missing[:20]}")
    return ordered


def self_test() -> None:
    item = {
        "ltEpsd": 1238, "ltRflYmd": "20260822",
        "tm1WnNo": 2, "tm2WnNo": 13, "tm3WnNo": 18,
        "tm4WnNo": 32, "tm5WnNo": 38, "tm6WnNo": 42, "bnsWnNo": 22,
        "wholEpsdSumNtslAmt": 114537798000,
        **{f"rnk{i}WnNope": i * 10 for i in range(1, 6)},
        **{f"rnk{i}WnAmt": i * 1000 for i in range(1, 6)},
    }
    row = parse_official_item(item, source_url="official", collected_at="2026-08-23T00:00:00+09:00")
    assert row.numbers_sorted == (2, 13, 18, 32, 38, 42)
    assert row.numbers_draw_order is None
    assert row.verification_status["numbers_draw_order"] == "unavailable_not_inferred"
    print("PASS lotto_collect: 공식 필드 · 추첨순서 미추정 · 원문 해시")


if __name__ == "__main__":
    self_test()
