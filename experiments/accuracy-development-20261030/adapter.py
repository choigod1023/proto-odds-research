"""Research-only provisional-v0 boundary; player effects are not fitted."""
from datetime import datetime, timedelta
from copy import deepcopy
import hashlib
import json
import math
import re



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


def _validate_lineup(lineup, ident, state, decision):
    _keys(lineup, ("official_game_id", "source_published_at_claim", "first_received_at", "revision_received_at", "source_url", "raw_sha256", "availability_tier", "external_receipt_verified", "sport", "players"))
    if lineup["official_game_id"] != ident["official_game_id"]:
        raise ValueError("game mismatch")
    published = _time(lineup["source_published_at_claim"]) if lineup["source_published_at_claim"] is not None else None
    first = _time(lineup["first_received_at"])
    revision = _time(lineup["revision_received_at"])
    if (published is not None and published > revision) or first > revision or revision > decision:
        raise ValueError("lineup not available at decision")
    _text(lineup["source_url"]); _hash(lineup["raw_sha256"])
    if lineup["availability_tier"] != "observed":
        raise ValueError("lineup not observed")
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


def infer(state, request, contract, verify_contract):
    """Infer only with an independently verified, state-bound core contract.

    verify_contract is a caller-supplied trust boundary, not an external witness
    implementation. It must authenticate the full contract, game identity, team
    mapping and the complete training provenance of the exact state. A boolean
    assertion, v1 schema validation or an untrusted self-hash is insufficient.
    """
    _keys(request, ("identity", "decision_at", "lineup"))
    ident = request["identity"]
    _keys(ident, ("league", "canonical_game_id", "official_game_id", "product_id", "home_team_id", "away_team_id", "kickoff_at"))
    for key in ("league", "canonical_game_id", "official_game_id", "product_id", "home_team_id", "away_team_id"):
        _text(ident[key])
    if ident["home_team_id"] == ident["away_team_id"] or ident["league"] != state.league:
        raise ValueError("inconsistent game identity")
    if state.league not in ("KBO", "NPB", "MLB", "K리그1", "K리그2") or state.kind not in ("score", "outcome"):
        raise ValueError("unsupported baseline")
    decision, kickoff = _time(request["decision_at"]), _time(ident["kickoff_at"])
    if decision > kickoff - timedelta(minutes=30):
        raise ValueError("decision after T-30")
    _keys(contract, ("identity", "product", "team_mapping", "state_sha256", "state_available_through", "quote", "decision_at"))
    if _time(contract["decision_at"]) != decision:
        raise ValueError("decision time does not match verified contract")
    if contract["identity"] != ident:
        raise ValueError("unverified game identity")
    product = contract["product"]
    _keys(product, ("product_id", "market", "period", "line", "outcomes"))
    outcomes = ["home", "draw", "away"] if state.soccer else ["home", "away"]
    if product != {"product_id": ident["product_id"], "market": "win", "period": "full", "line": None, "outcomes": outcomes}:
        raise ValueError("unsupported product")
    mapping = contract["team_mapping"]
    if not isinstance(mapping, dict) or set(mapping) != {ident["home_team_id"], ident["away_team_id"]}:
        raise ValueError("incomplete team mapping")
    home, away = mapping[ident["home_team_id"]], mapping[ident["away_team_id"]]
    if not isinstance(home, str) or not isinstance(away, str) or home == away or home not in state.index or away not in state.index:
        raise ValueError("unknown or ambiguous state team")
    fingerprint = hashlib.sha256(_canonical(state.export()).encode("utf-8")).hexdigest()
    if contract["state_sha256"] != fingerprint:
        raise ValueError("state fingerprint mismatch")
    available = _time(contract["state_available_through"])
    if state.clock is None or state.updates < 1 or available > decision or state.clock > decision or state.clock > available:
        raise ValueError("state contains future information")
    quote = contract["quote"]
    _keys(quote, ("product_id", "observed_at", "first_received_at", "revision_received_at", "availability_tier", "odds", "source_url", "source_sha256", "published_at_claim", "external_receipt_verified"))
    if quote["product_id"] != ident["product_id"] or quote["availability_tier"] != "observed":
        raise ValueError("quote product or availability mismatch")
    _text(quote["source_url"]); _hash(quote["source_sha256"])
    if quote["external_receipt_verified"] is not False:
        raise ValueError("no external witness verifier is implemented")
    observed, first, revision = (_time(quote[k]) for k in ("observed_at", "first_received_at", "revision_received_at"))
    published = _time(quote["published_at_claim"]) if quote["published_at_claim"] is not None else None
    if (published is not None and published > revision) or observed > revision or first > revision or revision > decision:
        raise ValueError("quote unavailable at decision")
    odds = quote["odds"]
    if not isinstance(odds, dict) or set(odds) != set(outcomes) or any(type(x) not in (float, int) or not math.isfinite(x) or x <= 1 for x in odds.values()):
        raise ValueError("invalid quote vector")
    if not callable(verify_contract) or verify_contract(deepcopy(contract), state.export()) is not True:
        raise ValueError("core contract verification failed")
    lineup = request["lineup"]
    reason = {"code": "lineup_unavailable"}
    accepted = None
    if lineup is not None:
        try:
            _validate_lineup(lineup, ident, state, decision)
        except (ValueError, TypeError, KeyError) as exc:
            reason = {"code": "lineup_invalid", "detail": str(exc)}
        else:
            accepted = lineup
            reason = {"code": "player_effects_not_fitted"}
    probabilities, diagnostics = state.predict(home, away, decision)
    envelope = {"schema_version": "research-adapter-v1", "identity": ident, "product": product,
                "decision_at": request["decision_at"], "model_id": "pr433-team-state",
                "model_version": "research-adapter-v1", "probability_vector": probabilities,
                "input_revision": accepted, "availability_reason": reason, "diagnostics": diagnostics,
                "baseline_state": state.export(), "core_contract": contract}
    envelope = json.loads(_canonical(envelope))
    envelope["freeze_hash"] = hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()
    return envelope
