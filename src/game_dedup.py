"""프로토 재발매로 회차만 다른 동일 경기 카드를 하나로 정리한다."""
from __future__ import annotations

import re


def event_key(game: dict) -> tuple[str, str, str, str, str]:
    def clean(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()
    return tuple(clean(game.get(field)) for field in
                 ("sport", "league", "date", "home", "away"))


def _quality(game: dict) -> tuple[int, int, int]:
    priced = sum(1 for option in game.get("options") or []
                 if float(option.get("배당") or 0) > 1)
    try:
        round_no = int(game.get("round") or 0)
    except (TypeError, ValueError):
        round_no = 0
    return int(priced > 0), int(game.get("status") != "배당대기"), round_no


def deduplicate_game_sections(document: dict) -> int:
    """live/past 전체에서 동일 이벤트의 가장 완전한 최신 발매만 남긴다."""
    chosen: dict[tuple, tuple[str, dict]] = {}
    removed = 0
    for section in ("live", "past"):
        for game in document.get(section) or []:
            key = event_key(game)
            previous = chosen.get(key)
            if previous is None or _quality(game) > _quality(previous[1]):
                removed += int(previous is not None)
                chosen[key] = (section, game)
            else:
                removed += 1
    document["live"] = [game for section, game in chosen.values() if section == "live"]
    document["past"] = [game for section, game in chosen.values() if section == "past"]
    return removed
