"""Display-only period scores and provider events; never used for settlement."""
import re


def named_match_progress(raw: dict, sport: str) -> dict:
    if raw.get("gameStatus") == "READY":
        return {}
    teams = raw.get("teams") or {}
    periods = {}
    for side in ("home", "away"):
        seen = set()
        for row in (teams.get(side) or {}).get("periodData") or []:
            period, score = row.get("period"), row.get("score")
            if type(period) is not int or not 1 <= period <= 50:
                continue
            valid = type(score) in (int, float) and score >= 0 and float(score).is_integer()
            value = int(score) if valid else None
            target = periods.setdefault(period, {"period": period, "home": None, "away": None})
            target[side] = None if period in seen else value
            seen.add(period)
    history = raw.get("broadcasts")
    complete = isinstance(history, list) and bool(history)
    source = history if complete else [raw.get("broadcast")]
    events, seen = [], set()
    for row in source:
        if not isinstance(row, dict) or not isinstance(row.get("playText"), str) or not row["playText"].strip():
            continue
        period = row.get("period")
        event = {"text": row["playText"].strip()[:500], "type": str(row.get("eventType") or ""),
                 "side": str(row.get("locationType") or "TOTAL").lower(),
                 "period": period if type(period) is int and 0 < period <= 50 else None}
        clock = str(row.get("displayTime") or "")
        # Soccer clocks are cumulative minutes encoded HH:MM, other clocks
        # remain provider text because quarter lengths and count directions vary.
        match = re.fullmatch(r"(\d{1,2}):([0-5]\d)", clock)
        if match and event["type"] not in {"FULLTIME", "HALFTIME", "END"}:
            minute = int(match[1]) * 60 + int(match[2])
            if sport == "soccer" and minute <= 150:
                event["time"] = f"{minute}′"
            elif sport == "basketball":
                event["time"] = clock
        identity = tuple(event.items())
        if identity not in seen:
            events.append(event)
            seen.add(identity)
    return {"period_scores": [periods[n] for n in sorted(periods)],
            "current_period": raw.get("period") if type(raw.get("period")) is int else None,
            "timeline": events, "timeline_scope": "available" if complete else "latest"}
