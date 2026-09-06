"""Archive the existing daily highlight policy, freezing exact offers at T-30."""
from datetime import datetime, timedelta, timezone
import hashlib
import math

KST = timezone(timedelta(hours=9))
POLICY = "daily-league-3-plus-60-v1"


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


def number(value, default=0):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def probability(row):
    p = number(row.get("predicted_hit_prob", row.get("final_probability")))
    return p if 0 < p < 1 else number(row.get("market_prob"))


def selection_key(row):
    return "|".join(str(row.get(k) or "") for k in
                    ("round", "game_no", "market", "market_label", "sel"))


def highlights(candidates):
    """Parity-tested against the browser's dailyHighlightedSelections."""
    groups = {}
    for row in candidates:
        kickoff = stamp(row.get("kickoff_at"))
        if (not kickoff or row.get("market") == "홀짝" or row.get("final_reversal") is True
                or row.get("is_market_favorite") is False
                or not 1 < number(row.get("odds")) < 2.2 or probability(row) < .55):
            continue
        group = (kickoff.astimezone(KST).date(), row.get("league") or "리그 미분류")
        groups.setdefault(group, []).append(row)
    result = set()
    for rows in groups.values():
        primary = [r for r in rows if number(r.get("odds")) >= 1.5]
        pool = sorted(primary or rows, key=lambda r: (
            -probability(r), -number(r.get("probability_lower_bound"), probability(r)),
            number(r.get("odds")), str(r.get("kickoff_at") or r.get("date") or ""), selection_key(r)))
        result.update(selection_key(r) for i, r in enumerate(pool) if i < 3 or probability(r) >= .6)
    return result


def capture_history(payload, previous, now):
    published = stamp(payload.get("generated_at"))
    history = dict((previous or {}).get("recommendation_history") or {})
    if not published or published > now:
        return history
    candidates = payload.get("candidates") or []
    chosen = highlights(candidates)
    for row in candidates:
        kickoff = stamp(row.get("kickoff_at"))
        # Old imported timestamps cannot fabricate a pregame publication today.
        if not kickoff or max(now, published) >= kickoff - timedelta(minutes=30):
            continue
        if not row.get("home") or not row.get("away"):
            continue
        event = "|".join([kickoff.astimezone(timezone.utc).isoformat(), str(row.get("sport")), str(row.get("league")),
                          row["home"], row["away"]])
        key = hashlib.sha256(event.encode()).hexdigest()[:24]
        fields = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
                  "market_label", "sel", "odds", "kickoff_at", "n_way")
        history[key] = {**{k: row.get(k) for k in fields}, "id": key,
                        "published_at": published.isoformat(), "recorded_at": now.isoformat(),
                        "recommended": selection_key(row) in chosen, "policy": POLICY,
                        "probability": probability(row)}
    # Retain every outcome equally. Empty/partial responses never erase history.
    return {k: r for k, r in history.items()
            if stamp(r.get("kickoff_at")) and stamp(r["kickoff_at"]) >= now - timedelta(days=90)}


def settle_history(history, odds, now):
    """Keep official results after the provider's rolling round leaves its feed."""
    for entry in history.values():
        kickoff = stamp(entry.get("kickoff_at"))
        if not kickoff or kickoff > now:
            continue
        rows = (odds or {}).get("markets", {}).get(str(entry.get("round")), {})
        matches = [r for r in rows.values() if all(r.get(k) == entry.get(k)
                   for k in ("home", "away", "date", "market"))
                   and (r.get("label") or "") == (entry.get("market_label") or "")
                   and str(r.get("game_no")) == str(entry.get("game_no"))]
        if len(matches) != 1:
            continue
        row = matches[0]
        outcome = row.get("result")
        if outcome in ("취소", "연기", "중단", "무효"):
            result = "void"
        else:
            three = row.get("n_way") == 3
            names = {"승패": ["홈", "원정"], "승무패": ["홈", "무", "원정"],
                     "핸디캡": ["핸디홈", "핸디무", "핸디원정"] if three else ["핸디홈", "핸디원정"],
                     "언더오버": ["언더", "오버"], "승①패": ["홈2+", "1점차", "원정2+"],
                     "승⑤패": ["홈6+", "5점차이내", "원정6+"], "홀짝": ["홀", "짝"]}.get(entry.get("market"))
            winners = ({"홈승": 0, "핸디승": 0, "무승부": 1, "핸디무": 1, "①": 1, "⑤": 1, "홈패": 2, "핸디패": 2}
                       if three else {"홈승": 0, "핸디승": 0, "언더": 0, "홀": 0, "홈패": 1, "핸디패": 1, "오버": 1, "짝": 1})
            if not names or len(names) != row.get("n_way") or entry.get("sel") not in names or outcome not in winners:
                continue
            result = "hit" if names[winners[outcome]] == entry["sel"] else "miss"
        entry.update(result=result, result_source="official", settled_at=now.isoformat())
    return history
