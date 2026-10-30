"""Research-only provisional-v0 boundary; player effects are not fitted."""
from datetime import datetime
import hashlib
import json
import math
import re

from baseline_model import State


def _keys(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        raise ValueError("unexpected or missing fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty string required")
    return value


def _time(value):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError
        return t
    except (ValueError, TypeError, AttributeError):
        raise ValueError("timezone-aware timestamp required") from None


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("lowercase SHA256 required")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def infer(state, request):
    """Return the unchanged PR433 state prediction and serializable research envelope.

    State team names must be the exact supplied identity IDs. Lineup is optional;
    a provided lineup must describe both teams completely in regulation units.
    No arbitrary features, outcomes or settlement fields are accepted.
    """
    _keys(request, ("identity", "decision_at", "lineup"))
    ident = request["identity"]
    _keys(ident, ("league", "official_game_id", "product_id", "home_team_id", "away_team_id", "kickoff_at"))
    for key in ("league", "official_game_id", "product_id", "home_team_id", "away_team_id"):
        _text(ident[key])
    if ident["home_team_id"] == ident["away_team_id"] or ident["league"] != state.league:
        raise ValueError("inconsistent game identity")
    if state.league not in ("KBO", "NPB", "MLB", "K리그1", "K리그2") or state.kind not in ("score", "outcome"):
        raise ValueError("unsupported baseline")
    decision, kickoff = _time(request["decision_at"]), _time(ident["kickoff_at"])
    if decision >= kickoff:
        raise ValueError("decision must precede kickoff")
    lineup = request["lineup"]
    status = "lineup_unavailable" if lineup is None else "player_effects_not_fitted"
    if lineup is not None:
        _keys(lineup, ("official_game_id", "source_published_at_claim", "first_received_at", "revision_received_at", "source_url", "raw_sha256", "availability_tier", "external_receipt_verified", "sport", "players"))
        if lineup["official_game_id"] != ident["official_game_id"]:
            raise ValueError("game mismatch")
        published = _time(lineup["source_published_at_claim"])
        first = _time(lineup["first_received_at"])
        revision = _time(lineup["revision_received_at"])
        if published > decision or first > revision or revision > decision:
            raise ValueError("lineup not available at decision")
        _text(lineup["source_url"]); _text(lineup["availability_tier"]); _hash(lineup["raw_sha256"])
        if lineup["external_receipt_verified"] is not False:
            raise ValueError("no external witness verifier is implemented")
        sport = lineup["sport"]
        if sport not in ("soccer", "baseball") or (sport == "soccer") != state.soccer:
            raise ValueError("sport mismatch")
        players = lineup["players"]
        if not isinstance(players, list) or not players:
            raise ValueError("players required")
        totals = {ident["home_team_id"]: 0.0, ident["away_team_id"]: 0.0}
        seen = set(); starters = {team: 0 for team in totals}; orders = {team: set() for team in totals}
        for player in players:
            _keys(player, ("team_id", "player_id", "role", "order", "announcement_status", "expected_minutes_or_innings"))
            team = player["team_id"]; pid = _text(player["player_id"])
            if team not in totals or pid in seen:
                raise ValueError("invalid team or duplicate player")
            seen.add(pid)
            if player["announcement_status"] not in ("confirmed", "probable"):
                raise ValueError("invalid announcement status")
            role = player["role"]; order = player["order"]
            if sport == "baseball":
                if role not in ("starter", "bullpen") or order is not None:
                    raise ValueError("baseball requires pitcher roles and no batting order")
                starters[team] += role == "starter"
                maximum = 9
            else:
                if role != "player" or (order is not None and (type(order) is not int or not 1 <= order <= 11 or order in orders[team])):
                    raise ValueError("invalid soccer role or order")
                if order is not None: orders[team].add(order)
                maximum = 90
            amount = player["expected_minutes_or_innings"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= maximum:
                raise ValueError("invalid playing allocation")
            totals[team] += amount
        expected = 990 if sport == "soccer" else 9
        if any(not math.isclose(v, expected, abs_tol=1e-8, rel_tol=0) for v in totals.values()):
            raise ValueError("incomplete team allocation")
        if sport == "baseball" and any(v != 1 for v in starters.values()):
            raise ValueError("exactly one starter per team required")
    probabilities, diagnostics = state.predict(ident["home_team_id"], ident["away_team_id"], decision)
    envelope = {"schema_version": "provisional-v0", "identity": ident, "decision_at": request["decision_at"],
                "model_id": "pr433-team-state", "model_version": "research-adapter-v0", "probability_vector": probabilities,
                "input_revision": lineup, "availability_reason": status, "diagnostics": diagnostics,
                "baseline_state": state.export()}
    envelope = json.loads(_canonical(envelope))
    envelope["freeze_hash"] = hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()
    return envelope
