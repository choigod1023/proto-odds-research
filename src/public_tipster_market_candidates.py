"""Conservative, offline candidate extraction; never changes operating votes.

Input is the already isolated recommendation section for an already verified
fixture. Callers must enforce source identity, timing and fixture matching.
"""
import math
import re
from decimal import Decimal, InvalidOperation

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_TOTAL = re.compile(rf"(?:\[언더오버\]|언더오버)\s*[:：]?\s*({_NUMBER})\s*(언더|오버)\s*$")
_HANDICAP = re.compile(rf"(?:\[핸디(?:캡)?\s*(2way|3way)\]|핸디(?:캡)?\s*(2way|3way))\s*[:：]?\s*(홈|원정|무)\s*({_NUMBER})\s*$", re.I)


def _line(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number * 2 != (number * 2).to_integral_value():
            return None
        return number
    except (InvalidOperation, ValueError, TypeError):
        return None


def extract_candidates(section):
    """Return explicit full-game selections; malformed/ambiguous families omitted.

    The input must be only the full-game recommendation section, not article
    analysis. Any first-half marker terminates it. Evidence is the source line.
    A handicap requires an explicit 2way/3way settlement and HOME line.
    """
    section = re.split(r"전반전|전반\s*(?:예측|추천)|글로벌 주요", section, maxsplit=1)[0]
    found = {"total": [], "handicap": []}
    for raw in section.splitlines():
        text = raw.strip()
        if re.search(r"전반|후반|쿼터|[1-4]\s*쿼터", text):
            continue
        family = "total" if "언더오버" in text else "handicap" if "핸디" in text else None
        if not family:
            continue
        m = (_TOTAL if family == "total" else _HANDICAP).fullmatch(text)
        if not m:
            found[family].append(None)
            continue
        if family == "total":
            line, side = _line(m[1]), {"언더": "under", "오버": "over"}[m[2]]
            settlement = "2way"
            if line is not None and line < 0:
                line = None
        else:
            settlement = (m[1] or m[2]).lower()
            side = {"홈": "home", "원정": "away", "무": "draw"}[m[3]]
            line = _line(m[4])
            if side == "draw" and settlement != "3way":
                line = None
        found[family].append(None if line is None else dict(
            family=family, side=side, line=float(line), period="full_game",
            settlement=settlement, evidence=text))
    return [items[0] for items in found.values() if len(items) == 1 and items[0] is not None]


def match_candidate(candidate, game):
    """Match exactly one complete option market; return its unmodified option.

    None means withheld. This does not validate the game identity or timestamp.
    Handicap label is a signed HOME line; opposing line is never inferred from
    an article's team-relative handicap.
    """
    family, side, settlement = (candidate.get(k) for k in ("family", "side", "settlement"))
    line = _line(candidate.get("line"))
    if candidate.get("period") != "full_game" or line is None:
        return None
    if family == "total" and settlement == "2way" and side in {"under", "over"} and line >= 0:
        market, names = "언더오버", {"언더", "오버"}
        selection = {"under": "언더", "over": "오버"}[side]
    elif family == "handicap" and settlement in {"2way", "3way"} and side in {"home", "away", "draw"}:
        if side == "draw" and settlement != "3way":
            return None
        market = "핸디캡"
        names = {"핸디홈", "핸디원정"} | ({"핸디무"} if settlement == "3way" else set())
        selection = {"home": "핸디홈", "away": "핸디원정", "draw": "핸디무"}[side]
    else:
        return None
    groups = {}
    for option in game.get("options", []):
        if option.get("market") != market:
            continue
        label = str(option.get("label", "")).strip()
        pattern = rf"H\s*({_NUMBER})" if family == "handicap" else rf"(?:[UO]\s*)?({_NUMBER})"
        label_match = re.fullmatch(pattern, label, re.I)
        if not label_match or _line(label_match[1]) != line:
            continue
        if option.get("line") is not None and _line(option["line"]) != line:
            return None
        groups.setdefault(str(option.get("게임번호", "")), []).append(option)
    matches = []
    for options in groups.values():
        if len(options) != len(names) or {o.get("선택") for o in options} != names:
            return None
        for option in options:
            try:
                price = float(option["배당"])
                if not math.isfinite(price) or price <= 1:
                    return None
            except (KeyError, TypeError, ValueError):
                return None
        matches.extend(o for o in options if o["선택"] == selection)
    return dict(matches[0]) if len(matches) == 1 else None
