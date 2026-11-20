"""Conservative research-only capture from an already collected document."""
from copy import deepcopy
from datetime import datetime, timedelta
from player_fixture_time import fixture_time
from player_info import canonical_team, _team_map


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def capture(game, document, *, observed_at, kickoff):
    base = dict(version="pregame-player-capture-v1", status="unavailable",
                observed_at=observed_at, affects_probability=False, training_eligible=False)
    def reject(reason):
        return {**base, "reason": reason}
    now, start = timestamp(observed_at), timestamp(kickoff)
    if now is None or start is None or now >= start-timedelta(minutes=30):
        return reject("not_before_t30")
    if not isinstance(document, dict):
        return reject("missing_player_document")
    if not isinstance(document.get("games"), list):
        return reject("invalid_player_document")
    # Use only the existing curated aliases; retain exact league/full kickoff.
    mapping = _team_map()
    def team(name):
        return canonical_team(game.get('league'), name, mapping)
    matches = [r for r in document.get("games", []) if isinstance(r, dict)
               and r.get("league") == game.get("league")
               and team(r.get("home_team")) == team(game.get("home"))
               and team(r.get("away_team")) == team(game.get("away"))
               and fixture_time(r.get("game_datetime"), r.get("league"), r.get("source")) == start]
    if len(matches) != 1:
        return reject("missing_or_ambiguous_fixture")
    row = matches[0]
    updated = timestamp(row.get("updated_at"))
    if updated is None or updated > now or not row.get("source") or not row.get("game_id"):
        return reject("missing_or_future_provenance")
    # Six hours is a capture freshness guard, not a model-validated threshold.
    if now-updated > timedelta(hours=6):
        return reject("stale_player_context")
    starters = row.get("starters") or {}
    lineups = row.get("lineups") or {}
    if (not isinstance(starters, dict) or not isinstance(lineups, dict)
            or any(not isinstance(starters.get(s, {}), dict) for s in ("home", "away"))):
        return reject("invalid_player_context")
    result = {**base, "status": "captured", "reason": "requires_feature_provenance_review",
            "source": row["source"], "source_url": row.get("source_url"),
            "source_game_id": row["game_id"], "source_updated_at": row["updated_at"],
            "kickoff": kickoff, "starters": deepcopy(starters),
            "starter_status": deepcopy(row.get("starter_status") or {}),
            "lineups": deepcopy(lineups), "lineup_status": deepcopy(row.get("lineup_status") or {}),
            "coverage": {"both_starters_named": all(bool((starters.get(s) or {}).get("name"))
                                                     for s in ("home", "away")),
                         "both_lineups_present": all(bool(lineups.get(s)) for s in ("home", "away"))},
            "limitations": ["Presence is not confirmed lineup status.",
                            "Document update time does not certify each statistic's as-of time."]}
    candidate = row.get('research_candidate_input')
    if isinstance(candidate,dict):
        available=timestamp(candidate.get('observed_at'))
        candidate_start=timestamp(candidate.get('start_at'))
        if (candidate.get('status')=='captured' and available is not None and available<=now
                and candidate_start==start and str(candidate.get('gamePk'))==str(row['game_id'])):
            result['candidate_input']=deepcopy(candidate)
        else:
            result['candidate_input_status']='unavailable_or_invalid_provenance'
            reason=candidate.get('reason')
            if isinstance(reason,str) and len(reason)<=128:
                result['candidate_input_reason']=reason
    return result
