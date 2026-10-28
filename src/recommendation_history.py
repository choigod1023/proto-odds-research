"""Archive the existing daily highlight policy, freezing exact offers at T-30."""
from datetime import datetime, timedelta, timezone
import hashlib
import math

KST = timezone(timedelta(hours=9))
POLICY = "daily-p55-o150-unbounded-v4"
MIN_HIT = .55


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
    result = set()
    for row in candidates:
        kickoff = stamp(row.get("kickoff_at"))
        if (not kickoff or row.get("market") == "홀짝" or row.get("final_reversal") is True
                or row.get("is_market_favorite") is False
                or not 1.5 <= number(row.get("odds")) or not MIN_HIT <= probability(row) < 1):
            continue
        result.add(selection_key(row))
    return result


def capture_history(payload, previous, now):
    published = stamp(payload.get("generated_at"))
    history = dict((previous or {}).get("recommendation_history") or {})
    if not published or published > now:
        return history
    candidates = list(payload.get("candidates") or []) + list(payload.get('selection_withholds') or [])
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
        if row.get('selection_policy') == 'tipster-vote-v1':
            # Keep each pregame revision for exact-price settlement. A newer
            # withhold revision supersedes admission without deleting old bets.
            revision = row.get('selection_revision') or row.get('decision_id')
            if not revision:
                continue
            key = hashlib.sha256((event+'|'+revision).encode()).hexdigest()[:24]
            if key in history:
                continue
        fields = ("home", "away", "sport", "league", "date", "round", "game_no", "market",
                  "market_label", "sel", "odds", "kickoff_at", "n_way",
                  "market_prob", "predicted_hit_prob", "probability_source",
                  "decision_id", "decision_model", "decision_artifact_hash",
                  "decision_pipeline_applied", "has_validated_edge",
                  "probability_method", "probability_version", "is_market_favorite", "price_source",
                  "selection_policy", "selection_policy_applied", "selection_revision", "tipster_selection")
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
        entry.update(result=result, result_source="official", settled_at=now.isoformat(),
                     official_market_result=outcome)
    return history


def attach_official_scores(history, connection):
    """Join indexed physical results, never handicap-adjusted or live scores.

    Look up only archived event keys, not the full results dataset. A later
    invalidation of an offer must also suppress its formerly confirmed score.
    """
    cache = {}
    for entry in history.values():
        entry.pop("official_score", None)
        kickoff = stamp(entry.get("kickoff_at"))
        if not kickoff or entry.get("result_source") != "official" or entry.get("result") not in ("hit", "miss"):
            continue
        key = (kickoff.astimezone(KST).replace(tzinfo=None).isoformat(),
               entry.get("league"), entry.get("sport"), entry.get("home"), entry.get("away"), entry.get("round"))
        if key not in cache:
            rows = connection.execute("""SELECT r.home_score,r.away_score,r.observed_at
                FROM match_results r WHERE r.source='proto' AND r.kickoff=?
                AND r.league=? AND r.sport=? AND r.home_team=? AND r.away_team=? AND r.round=?
                AND r.result_state='confirmed' AND NOT EXISTS (
                  SELECT 1 FROM match_results n WHERE n.source=r.source AND n.season=r.season
                  AND n.round=r.round AND n.game_no=r.game_no AND n.observed_at>r.observed_at)
                """, key).fetchall()
            scores = {(r[0], r[1]) for r in rows}
            cache[key] = {"score": list(next(iter(scores))), "source": "proto_full_game_result",
                          "observed_at": max(r[2] for r in rows), "kickoff_at": entry["kickoff_at"]} if len(scores) == 1 else None
        if cache[key]:
            entry["official_score"] = dict(cache[key])
    return history
