"""Small prospective baseline; never an operating probability or trained model."""
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

VERSION = "recent-form-poisson-shadow-v1"
KST = ZoneInfo("Asia/Seoul")


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


def candidate(game, *, observed_at, kickoff):
    """Use saved recent scores, not rounded display averages. Require both teams.

    Poisson score independence and conditioning away draws are exploratory
    assumptions, not calibrated claims. No extra DB reads, fitting or requests.
    """
    result = {"version": VERSION, "status": "unavailable", "probability": None,
              "affects_selection": False, "validated": False,
              "observed_at": observed_at}
    def reject(reason):
        return {**result, "reason": reason}
    now, start = stamp(observed_at), stamp(kickoff)
    if not now or not start or now >= start - timedelta(minutes=30):
        return reject("not_before_t30")
    snapshot = game.get("decision_snapshot") or {}
    selected = next((o for o in game.get("options", [])
                     if o.get("selection_id") and o.get("selection_id") == snapshot.get("selection_id")), {})
    if (game.get("sport") != "bs" or game.get("league") not in {"KBO", "NPB", "MLB"}
            or selected.get("market") != "승패" or selected.get("선택") not in {"홈", "원정"}):
        return reject("unsupported_selected_market")
    cutoff = stamp(game.get("form_before"))
    market = selected.get("시장확률")
    if isinstance(market, bool) or not isinstance(market, (int, float)) or not 0 < market < 1:
        return reject("invalid_market_probability")
    if game.get("form_src") != "database" or cutoff is None or cutoff > now:
        return reject("missing_or_future_form_provenance")
    inputs = {}
    for side in ("home", "away"):
        rows = (game.get("form_" + side) or {}).get("recent_games") or []
        if not 5 <= len(rows) <= 10:
            return reject("insufficient_recent_games")
        dates = [stamp(r.get("date")) for r in rows]
        if (any(d is None or d >= cutoff for d in dates)
                or len(set(dates)) != len(dates)):
            return reject("invalid_or_duplicate_result_times")
        for row in rows:
            if any(isinstance(row.get(k), bool) or not isinstance(row.get(k), (int, float))
                   or not math.isfinite(row[k]) or row[k] < 0 for k in ("gf", "ga")):
                return reject("invalid_scores")
        inputs[side] = [{"date": r["date"], "gf": r["gf"], "ga": r["ga"]} for r in rows]
    mean = lambda side, key: sum(r[key] for r in inputs[side]) / len(inputs[side])
    home = (mean("home", "gf") + mean("away", "ga")) / 2
    away = (mean("away", "gf") + mean("home", "ga")) / 2
    if not (0 < home <= 20 and 0 < away <= 20):
        return reject("invalid_scoring_rates")
    # A bounded 81-score vector suffices for this deliberately bounded baseline.
    def poisson(rate):
        values = [math.exp(-rate)]
        for i in range(1, 81):
            values.append(values[-1] * rate / i)
        return values
    h, a = poisson(home), poisson(away)
    home_win = sum(h[i] * sum(a[:i]) for i in range(81))
    away_win = sum(a[i] * sum(h[:i]) for i in range(81))
    prob = (home_win if selected["선택"] == "홈" else away_win) / (home_win + away_win)
    return {**result, "status": "shadow", "reason": "unvalidated_baseline",
            "probability": round(prob, 6), "selection_id": selected["selection_id"],
            "offer_id": selected.get("offer_id"), "market_probability": selected.get("시장확률"),
            "market_observed_at": snapshot.get("as_of"), "form_before": game["form_before"],
            "inputs": inputs, "lambda_home": home, "lambda_away": away,
            "source": "database_results_observed_at_capture",
            "assumptions": ["independent_poisson_scores", "condition_on_non_draw",
                            "no_pitcher_or_lineup_adjustment"]}
