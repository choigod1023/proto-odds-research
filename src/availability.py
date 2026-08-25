"""선수 출전 상태를 표준화하고 검증 전 영향도를 진단한다.

결장자 수를 승률에서 기계적으로 빼지 않는다. 선수 비중·결장 가능성·대체 수준·
출처 신뢰도를 분리해 저장하며, 과거 결장 데이터가 쌓이기 전까지는 설명과 경고에만
사용한다. 시장 배당에 이미 반영된 정보를 다시 더하는 이중계산도 피한다.
"""
from __future__ import annotations

import re

REASONS = {
    "injury": "부상", "suspension": "출장정지", "cards": "경고누적",
    "rest": "휴식", "illness": "질병", "registration": "미등록",
    "personal": "개인 사유", "unknown": "사유 미확인",
}


def _reason(row: dict) -> str:
    explicit = str(row.get("reason_code") or "").lower()
    if explicit in REASONS:
        return explicit
    text = " ".join(str(row.get(k) or "") for k in ("status", "reason", "detail")).lower()
    rules = (
        ("cards", r"경고.?누적|yellow.?card"),
        ("suspension", r"출장.?정지|징계|suspend|ban"),
        ("injury", r"부상|injur|disabled|il\b|day list"),
        ("illness", r"질병|illness|sick|covid"),
        ("rest", r"휴식|rest|관리"),
        ("registration", r"미등록|명단.?제외|not.?registered"),
        ("personal", r"개인.?사유|personal"),
    )
    return next((code for code, pattern in rules if re.search(pattern, text)), "unknown")


def _absence_probability(row: dict, reason: str) -> float:
    if row.get("availability_probability") is not None:
        return max(0.0, min(1.0, float(row["availability_probability"])))
    text = " ".join(str(row.get(k) or "") for k in ("status", "availability_status")).lower()
    if re.search(r"벤치|available|probable", text):
        return 0.1
    if re.search(r"questionable|출전.?의심", text):
        return 0.5
    if re.search(r"doubtful", text):
        return 0.75
    if reason in {"injury", "suspension", "cards", "registration"} or re.search(r"out|명단.?미포함", text):
        return 1.0
    return 0.5


def _importance(row: dict, sport: str, key_names: set[str]) -> float:
    if row.get("importance") is not None:
        return max(0.0, min(1.0, float(row["importance"])))
    if str(row.get("name") or "") in key_names:
        return 0.75
    if row.get("starter") is True:
        return 0.7
    position = str(row.get("position") or "").upper()
    if sport == "bs" and position in {"P", "SP"}:
        return 0.7
    if sport == "sc" and position in {"GK", "FW", "ST"}:
        return 0.55
    return 0.35


def _confidence(row: dict) -> float:
    if row.get("source_confidence") is not None:
        return max(0.0, min(1.0, float(row["source_confidence"])))
    source = str(row.get("source_type") or "").lower()
    return {"official_injury_report": .98, "official_roster": .95,
            "official_discipline": .98, "official_lineup": .9,
            "team_report": .8, "media": .55, "inferred": .3}.get(source, .5)


def enrich_availability(info: dict | None) -> dict | None:
    if not info:
        return info
    sport = str(info.get("sport") or "")
    unavailable = info.get("unavailable") or {}
    key_players = info.get("key_players") or {}
    burdens = {}
    enriched = {}
    for side in ("home", "away"):
        key_names = {str(x.get("name")) for x in key_players.get(side, []) if x.get("name")}
        rows = []
        for original in unavailable.get(side, []) or []:
            if not isinstance(original, dict) or not original.get("name"):
                continue
            row = dict(original)
            reason = _reason(row)
            probability = _absence_probability(row, reason)
            importance = _importance(row, sport, key_names)
            replacement_gap = max(0.0, min(1.0, float(row.get("replacement_gap", .5))))
            confidence = _confidence(row)
            impact = probability * importance * replacement_gap * confidence
            row.update({
                "reason_code": reason, "reason_label": REASONS[reason],
                "availability_probability": round(probability, 2),
                "importance": round(importance, 2), "source_confidence": round(confidence, 2),
                "impact_score": round(impact, 3),
                "impact_label": "큼" if impact >= .3 else "중간" if impact >= .12 else "작음",
            })
            rows.append(row)
        enriched[side] = sorted(rows, key=lambda row: row["impact_score"], reverse=True)
        burdens[side] = round(min(2.0, sum(row["impact_score"] for row in rows)), 3)
    diff = round(burdens.get("home", 0) - burdens.get("away", 0), 3)
    direction = "away" if diff >= .12 else "home" if diff <= -.12 else None
    info = dict(info)
    info["unavailable"] = enriched
    info["availability_summary"] = {
        "home_burden": burdens.get("home", 0), "away_burden": burdens.get("away", 0),
        "burden_difference": diff, "leans": direction,
        "model_adjustment": 0,
        "model_note": "과거 결장 백테스트 전에는 확률을 직접 보정하지 않고 불확실성 설명에만 사용",
    }
    return info
