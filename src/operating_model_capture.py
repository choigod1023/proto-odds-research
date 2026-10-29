"""Pure, bounded research controls attached to a NEW immutable prediction.

No model is trained or promoted here. Clock, market observation and capture
eligibility belong to the enclosing ledger, not to a reconstructed timestamp.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import math
from types import CodeType, MappingProxyType


VERSION = "operating-model-comparison-v1"
MAX_BYTES = 8192
RECIPES = MappingProxyType({
    name: MappingProxyType(dict(id=f"{name}-v1", role=role, rule=rule,
                               tie_break="selection_id_ascending",
                               saved_probability_sum_tolerance=0.0002,
                               vote_share_sum_tolerance=0.000002))
    for name, role, rule in (
        ("operating", "observed_operating", "snapshot_selection_and_final_verbatim"),
        ("market_reference", "comparison_control", "argmax_saved_market_probability"),
        ("fixed91_same_electorate", "frozen_comparison", "argmax_0.9_market_plus_0.1_vote"),
        ("saved_score_reference", "unvalidated_reference", "argmax_saved_score_model_probability"),
        ("home_always", "null_control", "home_without_probability"),
    )
})
_SIDES = MappingProxyType({"홈": "홈", "승": "홈", "원정": "원정", "패": "원정", "무": "무"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def recipe(arm):
    """Return a detached, JSON-serializable frozen recipe for hash auditing."""
    return dict(RECIPES[arm])


def _number(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (0 < value < 1 if positive else 0 <= value <= 1))


def _text(value, limit=256):
    if value is not None and (not isinstance(value, str) or len(value) > limit):
        raise ValueError("invalid_or_oversized_metadata")
    return value


def _time(value, field, *, required=False):
    if value is None and not required:
        return None
    try:
        value = _text(value, 64)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValueError(f"invalid_{field}") from None
    return value  # Preserve source text, never infer or substitute a timestamp.


def _input_number(value):
    # A stable invalid marker lets each arm explain its own nonfinite vector.
    if isinstance(value, float) and not math.isfinite(value):
        return "nonfinite"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value, 64)


def _arm(name):
    return dict(status="unavailable", reason="not_evaluated",
                role=RECIPES[name]["role"], recipe_id=RECIPES[name]["id"],
                recipe_hash=_hash(recipe(name)))


def _unavailable(payload, reason):
    payload.update(status="unavailable", reason=reason)
    for arm in payload["arms"].values():
        arm.update(status="unavailable", reason=reason)
    return payload


def _select(arm, row, probability, basis):
    arm.update(status="available", reason=None,
               **{k: row.get(k) for k in ("selection_id", "offer_id", "market", "label", "selection", "odds")},
               probability=probability, probability_basis=basis)


def _roster(options):
    if not isinstance(options, (list, tuple)):
        raise ValueError("missing_options")
    roster = []
    selection_ids, offer_ids, sides, markets = set(), set(), set(), set()
    # One pass over this game's options. At most three rows are retained.
    for row in options:
        if not isinstance(row, Mapping):
            raise ValueError("invalid_option")
        if row.get("market") not in {"승패", "승무패"} or row.get("label") not in (None, ""):
            continue
        if row.get("line") is not None:
            raise ValueError("non_full_game_line")
        sid, oid = row.get("selection_id"), row.get("offer_id")
        if not _text(sid, 128) or not _text(oid, 128):
            raise ValueError("missing_selection_or_offer_id")
        side = _SIDES.get(row.get("selection"))
        if side is None:
            raise ValueError("unsupported_full_game_selection")
        if sid in selection_ids:
            raise ValueError("duplicate_selection_id")
        if oid in offer_ids:
            raise ValueError("duplicate_offer_id")
        if side in sides:
            raise ValueError("duplicate_side_or_offer")
        selection_ids.add(sid)
        offer_ids.add(oid)
        sides.add(side)
        markets.add(row["market"])
        odds = row.get("odds")
        if (isinstance(odds, bool) or not isinstance(odds, (int, float))
                or not math.isfinite(odds) or odds <= 1):
            raise ValueError("invalid_odds")
        roster.append({k: row.get(k) for k in (
            "selection_id", "offer_id", "market", "label", "selection", "odds",
            "market_probability", "score_model_probability")})
        if len(roster) > 3:
            raise ValueError("multiple_or_oversized_full_game_roster")
    if len(markets) != 1:
        raise ValueError("missing_or_multiple_full_game_markets")
    expected = {"홈", "원정"} if markets == {"승패"} else {"홈", "무", "원정"}
    if sides != expected or len(roster) != len(expected):
        raise ValueError("incomplete_full_game_roster")
    return sorted(roster, key=lambda row: row["selection_id"])


def _vector(roster, key, *, market=False):
    values = [r.get(key) for r in roster]
    if any(v is None for v in values):
        return None, f"missing_{key}"
    if not all(_number(v, positive=market) for v in values):
        return None, f"invalid_{key}"
    # Both saved probability vectors may have been rounded to four decimals.
    tolerance = 0.0002
    if abs(sum(values) - 1) > tolerance:
        return None, f"invalid_{key}_sum"
    return values, None


def _argmax(roster, values):
    # Roster is already sorted by selection_id; max keeps the first exact tie.
    return max(range(len(roster)), key=lambda i: values[i])


def _capture(snapshot, features, payload):
    if not isinstance(snapshot, Mapping) or not isinstance(features, Mapping):
        raise ValueError("invalid_snapshot_or_features")
    if not _text(snapshot.get("decision_id"), 128):
        raise ValueError("missing_decision_id")
    audit = snapshot.get("audit") or {}
    model = snapshot.get("model") or {}
    hybrid = features.get("tipster_hybrid") or {}
    score = features.get("score_inputs") or {}
    fixture = features.get("fixture") or {}
    if not all(isinstance(v, Mapping) for v in (audit, model, hybrid, score, fixture)):
        raise ValueError("invalid_source_metadata")
    payload.update(
        prediction_as_of=_time(snapshot.get("as_of"), "prediction_as_of", required=True),
        source_generated_at=_time(audit.get("built_at"), "source_generated_at"),
        source_version=_text(snapshot.get("schema_version")),
        timing_source="outer_ledger",
        eligibility="requires_outer_ledger_timing_audit",
    )
    if audit.get("reconstructed_at") and payload["capture_kind"] == "prospective_same_snapshot":
        raise ValueError("retrospective_snapshot_unsupported")
    operating = payload["arms"]["operating"]
    operating["meta"] = {
        "selection_policy": _text(model.get("selection_policy") or hybrid.get("version")),
        "operating_version": _text(model.get("operating_version")),
        "source_artifact_hash": _text(model.get("artifact_hash")),
        "probability_interpretation": "market_reference_not_vote_share"
        if model.get("selection_policy") == "tipster-vote-v1" or hybrid.get("version") == "tipster-vote-v1"
        else "saved_snapshot_final",
    }
    final = (snapshot.get("probability") or {}).get("final")
    operating.update(selection_id=_text(snapshot.get("selection_id"), 128),
                     offer_id=_text(snapshot.get("offer_id"), 128),
                     probability=final if _number(final) else None)
    roster = _roster(features.get("options"))
    payload["price_roster_hash"] = _hash([
        {k: r[k] for k in ("selection_id", "offer_id", "market", "label", "selection", "odds")}
        for r in roster])
    payload["roster_size"] = len(roster)
    # Hash only the compact inputs used here; full originals stay in features.
    shares = hybrid.get("vote_shares")
    compact_shares = ({side: _input_number(shares.get(side)) for side in ("홈", "무", "원정")}
                      if isinstance(shares, Mapping) else None)
    batch = dict(roster=[{k: _input_number(v) if k.endswith("probability") else v
                         for k, v in row.items()} for row in roster],
        fixture={k: _text(str(fixture[k])) if fixture.get(k) is not None else None for k in (
            "sport", "league", "home", "away", "year", "round", "date", "source_event_id", "match_id")},
        snapshot={k: _text(snapshot.get(k), 128) for k in (
            "decision_id", "event_id", "as_of", "input_revision_hash", "action", "selection_id", "offer_id")},
        model=operating["meta"], final=operating["probability"],
        source_generated_at=payload["source_generated_at"],
        hybrid={**{k: _text(hybrid.get(k)) for k in (
            "version", "status", "reason", "evidence_hash", "latest_observed_at")},
            "authors": _input_number(hybrid.get("authors")), "vote_shares": compact_shares},
        score_inputs={"home": _input_number(score.get("home")),
                      "away": _input_number(score.get("away")), "source": _text(score.get("source"))})
    payload["input_batch_hash"] = _hash(batch)
    market, market_reason = _vector(roster, "market_probability", market=True)
    if snapshot.get("action") == "withhold" or not operating["selection_id"] or not operating["offer_id"]:
        operating.update(status="abstain", reason="operating_withheld_or_missing_selection")
    else:
        row = next((r for r in roster if r["selection_id"] == operating["selection_id"]
                    and r["offer_id"] == operating["offer_id"]), None)
        if row is None:
            operating["reason"] = "operating_selection_outside_shared_roster"
        elif not _number(final):
            operating["reason"] = "missing_or_invalid_operating_probability"
        elif any(k in snapshot and snapshot[k] != row[k] for k in ("market", "label")):
            operating["reason"] = "operating_market_or_label_mismatch"
        else:
            _select(operating, row, final, operating["meta"]["probability_interpretation"])
    reference = payload["arms"]["market_reference"]
    if market is None:
        reference["reason"] = market_reason
    else:
        i = _argmax(roster, market)
        _select(reference, roster[i], market[i], "saved_market_probability")
    fixed = payload["arms"]["fixed91_same_electorate"]
    fixed["meta"] = dict(source_version=_text(hybrid.get("version")),
                         evidence_hash=_text(hybrid.get("evidence_hash")),
                         interpretation="frozen_current_electorate_not_deployed_policy_replay")
    authors = hybrid.get("authors")
    if (not isinstance(authors, int) or isinstance(authors, bool) or authors <= 0
            or not isinstance(shares, Mapping)
            or not (hybrid.get("status") == "applied" or (
                hybrid.get("status") == "withheld" and hybrid.get("reason") == "tied_tipster_votes"))):
        fixed["reason"] = "no_valid_votes"
    elif market is None:
        fixed["reason"] = market_reason
    elif not fixed["meta"]["source_version"]:
        fixed["reason"] = "missing_vote_source_version"
    else:
        vote_values = [shares.get(_SIDES[r["selection"]]) for r in roster]
        if (not all(_number(v) for v in vote_values)
                or len(shares) > 3
                or set(shares) - {"홈", "무", "원정"}
                or (len(roster) == 2 and shares.get("무", 0) != 0)
                or abs(sum(vote_values) - 1) > 0.000002):
            fixed["reason"] = "invalid_vote_vector"
        else:
            try:
                fixed["meta"]["latest_observed_at"] = _time(
                    hybrid.get("latest_observed_at"), "vote_observed_at", required=True)
                if datetime.fromisoformat(fixed["meta"]["latest_observed_at"].replace("Z", "+00:00")) > datetime.fromisoformat(payload["prediction_as_of"].replace("Z", "+00:00")):
                    raise ValueError("vote_observed_after_prediction_as_of")
            except ValueError as exc:
                fixed["reason"] = str(exc)
            else:
                fixed["meta"]["authors"] = authors
                probabilities = [0.9 * p + 0.1 * v for p, v in zip(market, vote_values)]
                i = _argmax(roster, probabilities)
                _select(fixed, roster[i], probabilities[i], "frozen_market90_vote10")
    saved_score = payload["arms"]["saved_score_reference"]
    saved_score["meta"] = dict(source=_text(score.get("source")),
                               artifact_identity="unverified", training_evidence="unverified",
                               promoted=False)
    values, reason = _vector(roster, "score_model_probability")
    if values is None:
        saved_score["reason"] = reason
    else:
        i = _argmax(roster, values)
        _select(saved_score, roster[i], values[i], "saved_score_model_probability")
    home = next(r for r in roster if _SIDES[r["selection"]] == "홈")
    _select(payload["arms"]["home_always"], home, None, "not_applicable")
    payload.update(status="captured", reason=None)
    return payload


def capture(snapshot, features, *, capture_kind="prospective_same_snapshot"):
    """Capture five same-roster arms; failures cannot alter operating decisions.

    The runtime uses the default only for fresh ledger appends. Evaluators MUST
    use retrospective_same_snapshot_control for replay. Neither mode certifies
    pregame eligibility nor fills in the outer captured_at.
    """
    kind = capture_kind if capture_kind in (
        "prospective_same_snapshot", "retrospective_same_snapshot_control") else "unsupported"
    payload = dict(record_version=VERSION, capture_kind=kind,
                   affects_selection=False, affects_probability=False,
                   implementation_hash=IMPLEMENTATION_HASH,
                   arms={name: _arm(name) for name in RECIPES})
    try:
        if kind == "unsupported":
            raise ValueError("unsupported_capture_kind")
        payload = _capture(snapshot, features, payload)
        encoded = _json(payload)
        if len(encoded.encode("utf-8")) > MAX_BYTES:
            raise ValueError("capture_size_budget_exceeded")
        return json.loads(encoded)  # Detach every returned object from inputs.
    except Exception as exc:
        # This optional research sidecar must never make production fail. Do not
        # leak exception text containing input content into the immutable ledger.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).isidentifier() else "unsupported_input"
        fallback = dict(record_version=VERSION, capture_kind=kind,
                        affects_selection=False, affects_probability=False,
                        implementation_hash=IMPLEMENTATION_HASH,
                        arms={name: _arm(name) for name in RECIPES})
        return _unavailable(fallback, reason)


def _code_artifact(code):
    """Path/line independent function artifact; interpreter bytecode specific."""
    return dict(bytecode=code.co_code.hex(), names=code.co_names, variables=code.co_varnames,
                constants=[_code_artifact(v) if isinstance(v, CodeType)
                           else sorted(v) if isinstance(v, frozenset)
                           else v if v is None or isinstance(v, (str, int, float, tuple))
                           else type(v).__name__ for v in code.co_consts])


IMPLEMENTATION_HASH = _hash({
    "version": VERSION,
    "functions": {f.__name__: _code_artifact(f.__code__) for f in (
        _json, _hash, recipe, _number, _text, _time, _input_number, _arm, _unavailable, _select,
        _roster, _vector, _argmax, _capture, capture)},
    "recipes": {name: recipe(name) for name in RECIPES}, "sides": dict(_SIDES),
    "max_bytes": MAX_BYTES,
})
