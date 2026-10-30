"""Read-only, outcome-blind input contract for operating-ledger comparisons.

No storage, model execution, settlement, ROI, or hash-chain authentication occurs
here. See docs/research/2026-10-29-operating-input-contract.md for the contract.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

CONTRACT_VERSION = "operating-ledger-input-v1"
VOTE_POLICY = "tipster-vote-v1"


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _time(value: Any) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _stamp(value: Any) -> str | None:
    parsed = _time(value)
    return parsed.isoformat() if parsed else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _unpack(record: Mapping) -> dict:
    """Accept decoded ledger records or export rows containing record_json."""
    if "record_json" not in record:
        return deepcopy(dict(record))
    raw = record["record_json"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, Mapping):
        raise ValueError("record_json must contain an object")
    # SQL wrapper columns are not trusted overrides of the immutable payload.
    return deepcopy(dict(raw))


def _feature_findings(value: Any, as_of: datetime | None, path="features") -> tuple[list, list]:
    """Reject current-result-shaped inputs; preserve runtime's historical form.

    The historical context roots below are runtime's stored past-performance
    inputs, not labels for the fixture. This is a structural check, not proof
    that arbitrary opaque model inputs are free of leakage.
    """
    errors, warnings = [], []
    result_keys = {"result", "outcome", "winner", "final_score", "current_result", "external_result",
                   "current_event_result", "official_result", "settlement", "정산", "결과"}
    historical_keys = {"home_form", "away_form", "head_to_head"}
    pending = {"", "pending", "scheduled", "unknown", "unsettled", "경기전", "미정"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in historical_keys and path == "features.team_context":
                continue
            metadata_keys = {"version", "observed_at", "status", "affects_probability", "reason", "training_eligible"}
            metadata_only = (child == "features.research_player_inputs" and isinstance(item, Mapping)
                             and set(item) <= metadata_keys and item.get("status") == "unavailable"
                             and item.get("affects_probability") is False
                             and item.get("training_eligible") is False)
            if metadata_only:
                # Runtime can append an unavailable-capture marker after the
                # snapshot. It contains no player/model inputs or result. Do
                # not mistake the marker's timestamp for an active input clock.
                found, notes = _feature_findings(item, as_of, child)
                warnings.extend(reason.replace("feature_after_as_of:", "non_input_metadata_after_as_of:")
                                for reason in found if reason.startswith("feature_after_as_of:"))
                errors.extend(reason for reason in found if not reason.startswith("feature_after_as_of:"))
                warnings.extend(notes)
                continue
            if key in result_keys:
                if item is None or (isinstance(item, str) and item.lower() in pending):
                    warnings.append(f"unconfirmed_result_feature:{child}")
                else:
                    errors.append(f"result_in_features:{child}")
                continue
            if key in {"observed_at", "feature_cutoff_at", "latest_observed_at", "collected_at",
                       "captured_at", "published_at", "available_at", "updated_at"} and item is not None:
                instant = _time(item)
                if instant is None:
                    errors.append(f"invalid_feature_time:{child}")
                elif as_of is not None and instant > as_of:
                    errors.append(f"feature_after_as_of:{child}")
            if key in {"status", "result_status"} and isinstance(item, str) and item.lower() in {"finished", "final", "completed", "종료"}:
                errors.append(f"result_in_features:{child}")
            found, notes = _feature_findings(item, as_of, child)
            errors.extend(found)
            warnings.extend(notes)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found, notes = _feature_findings(item, as_of, f"{path}[{index}]")
            errors.extend(found)
            warnings.extend(notes)
    return errors, warnings


def normalize_operating_prediction(record: Mapping) -> dict:
    """Return a canonical observation INCLUDING its exclusion_reasons.

    Timezones are mandatory. This single-row API enforces pre-kickoff clocks;
    ``latest_pre_cutoff`` additionally enforces strict pre-T30 capture. Missing
    probabilities are allowed for ROI inputs, but missing prices/identities are
    not. No selected side, policy, artifact, probability, or result is guessed.
    """
    try:
        row = _unpack(record)
    except (TypeError, ValueError):
        return {"contract_version": CONTRACT_VERSION, "exclusion_reasons": ["invalid_record_json"],
                "roi_input_eligible": False, "result_used": False}
    reasons, warnings = [], []
    if row.get("record_type") != "prediction":
        reasons.append("not_prediction")
    for key in ("event_id", "snapshot_id"):
        if not isinstance(row.get(key), str) or not row[key]:
            reasons.append(f"missing_{key}")
    if row.get("decision_id") is not None and row["decision_id"] != row.get("snapshot_id"):
        reasons.append("decision_snapshot_id_mismatch")
    clocks = {key: _time(row.get(key)) for key in ("kickoff", "as_of", "captured_at", "market_observed_at")}
    for key, value in clocks.items():
        if value is None:
            reasons.append(f"missing_or_invalid_time:{key}")
    if all(clocks.values()):
        if not clocks["market_observed_at"] <= clocks["as_of"] <= clocks["captured_at"]:
            reasons.append("inconsistent_observation_order")
        if max(clocks[k] for k in ("as_of", "captured_at", "market_observed_at")) >= clocks["kickoff"]:
            reasons.append("not_before_kickoff")

    features = _mapping(row.get("features"))
    fixture = _mapping(features.get("fixture"))
    for key in ("sport", "league", "home", "away"):
        if not fixture.get(key):
            reasons.append(f"missing_fixture:{key}")
    model, versions = _mapping(row.get("model")), _mapping(row.get("versions"))
    version = model.get("operating_version")
    policy = model.get("selection_policy")
    if not isinstance(version, str) or not version:
        reasons.append("unknown_model_identity")
    if versions.get("operating_model") is not None and versions["operating_model"] != version:
        reasons.append("model_version_mismatch")
    hybrid = _mapping(features.get("tipster_hybrid"))
    if hybrid.get("status") == "applied" and hybrid.get("version") != version:
        reasons.append("policy_version_mismatch")
    vote_context = VOTE_POLICY in (policy, version, hybrid.get("version"))
    if vote_context:
        if hybrid.get("version") != VOTE_POLICY or policy not in {None, VOTE_POLICY}:
            reasons.append("policy_version_mismatch")
        if hybrid.get("status") == "applied":
            if policy != VOTE_POLICY or version != VOTE_POLICY:
                reasons.append("policy_version_mismatch")
            if not (model.get("affects_selection") is True and model.get("affects_probability") is False
                    and model.get("policy_authorized") is True and model.get("status") == "operational"):
                reasons.append("vote_policy_identity_inconsistent")
        else:
            reasons.append("vote_policy_not_applied")
    if policy is None:
        warnings.append("selection_policy_not_saved_operating_version_only")
    artifacts = [v for v in (model.get("artifact_hash"), model.get("model_artifact_hash"),
                              row.get("model_artifact_hash")) if v is not None]
    if any(v != artifacts[0] for v in artifacts[1:]):
        reasons.append("model_artifact_hash_mismatch")
    artifact = artifacts[0] if artifacts else None
    hashes = [v for v in (row.get("model_hash"), model.get("model_hash")) if v is not None]
    if any(v != hashes[0] for v in hashes[1:]):
        reasons.append("model_hash_mismatch")
    if any(not isinstance(v, str) or not v for v in artifacts + hashes):
        reasons.append("invalid_saved_model_hash")
    if artifact is None:
        warnings.append("model_artifact_hash_not_saved")
    identity = {"policy": policy, "model_version": version, "model_artifact_hash": artifact,
                "model_hash": hashes[0] if hashes else None,
                "feature_policy_version": hybrid.get("version"),
                "feature_policy_status": hybrid.get("status"),
                "selection_source": hybrid.get("selection_source") or "tipster_votes",
                "residual_version": model.get("residual_version"),
                "probability_pipeline_version": model.get("probability_pipeline_version")}
    if identity["model_hash"] is None:
        warnings.append("model_hash_not_saved")

    pred = _mapping(row.get("predictions"))
    sid, oid = pred.get("selection_id"), pred.get("offer_id")
    if not sid or not oid:
        reasons.append("missing_selected_selection_or_offer")
    raw_options = features.get("options")
    options = [dict(o) for o in raw_options if isinstance(o, Mapping)] if isinstance(raw_options, list) else []
    if not options or len(options) != len(raw_options):
        reasons.append("missing_or_invalid_offer_roster")
    ids = [o.get("offer_id") for o in options]
    if any(not i for i in ids):
        reasons.append("missing_roster_offer_id")
    if len(ids) != len(set(str(i) for i in ids)):
        reasons.append("duplicate_offer")
    matches = [o for o in options if o.get("selection_id") == sid and o.get("offer_id") == oid]
    selected = matches[0] if len(matches) == 1 and sid and oid else {}
    if not selected:
        reasons.append("selection_offer_mismatch")
    for key in ("market", "label", "selection", "odds"):
        if key in pred and selected and pred[key] != selected.get(key):
            reasons.append(f"selected_field_mismatch:{key}")
    market, label, line = selected.get("market"), selected.get("label"), selected.get("line")
    roster = [o for o in options if (o.get("market"), o.get("label"), o.get("line")) == (market, label, line)] if selected else []
    if not market or not selected.get("selection"):
        reasons.append("missing_selected_market_or_side")
    if len(roster) < 2:
        reasons.append("incomplete_same_market_roster")
    sides = [o.get("selection") for o in roster]
    expected = {"승패": {"홈", "원정"}, "승무패": {"홈", "무", "원정"},
                "언더오버": {"언더", "오버"}}.get(market)
    if expected is not None and set(str(s) for s in sides) != expected:
        reasons.append("incomplete_same_market_roster")
    if len(sides) != len(set(str(s) for s in sides)):
        reasons.append("duplicate_market_side")
    if any(not o.get("selection_id") or not o.get("selection") for o in roster):
        reasons.append("missing_roster_selection_identity")
    if any(_number(o.get("odds")) is None or _number(o["odds"]) <= 1 for o in roster):
        reasons.append("missing_or_invalid_roster_odds")
    odds = _number(selected.get("odds"))
    if odds is None or odds <= 1:
        reasons.append("missing_or_invalid_selected_odds")
    if pred.get("action") in {None, "withhold"}:
        reasons.append("no_operating_selection")
    if version == VOTE_POLICY and selected:
        if hybrid.get("selected_side") != selected.get("selection"):
            reasons.append("policy_selection_mismatch")
        if market not in {"승패", "승무패"} or label or line is not None:
            reasons.append("vote_policy_market_mismatch")
        if hybrid.get("selection_source") == "market_fallback":
            probabilities = {o.get("selection"): _number(o.get("market_probability")) for o in roster}
            if (hybrid.get("reason") != "market_fallback_no_votes" or hybrid.get("authors") != 0
                    or hybrid.get("vote_counts") is not None or hybrid.get("vote_shares") is not None
                    or any(v is None or not 0 < v < 1 for v in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > .01):
                reasons.append("invalid_market_fallback_evidence")
            else:
                leaders = [side for side, value in probabilities.items() if value == max(probabilities.values())]
                if len(leaders) != 1 or leaders[0] != selected.get("selection"):
                    reasons.append("policy_market_selection_mismatch")
        else:
            counts = _mapping(hybrid.get("vote_counts"))
            values = {side: _number(counts.get(side)) for side in sides}
            if not values or any(v is None or v < 0 or not v.is_integer() for v in values.values()):
                reasons.append("invalid_saved_vote_counts")
            else:
                leaders = [side for side, count in values.items() if count == max(values.values())]
                if len(leaders) != 1 or leaders[0] != selected.get("selection"):
                    reasons.append("policy_vote_selection_mismatch")

    raw_probability = pred.get("probability")
    detail = _mapping(raw_probability) if isinstance(raw_probability, Mapping) else _mapping(pred.get("probability_detail"))
    raw_probability = detail.get("final") if isinstance(raw_probability, Mapping) else raw_probability
    probability = _number(raw_probability)
    if raw_probability is not None and (probability is None or not 0 <= probability <= 1):
        reasons.append("invalid_probability")
        probability = None
    for other in (detail.get("final"), selected.get("final_probability")):
        if raw_probability is not None and other is not None and other != raw_probability:
            reasons.append("selected_probability_mismatch")
    basis = detail.get("basis")
    kind = "missing" if raw_probability is None else "unclassified_recorded_probability"
    if raw_probability is not None and basis in {"shin_market", "market"}:
        kind = "market_reference"
    elif raw_probability is not None and basis:
        kind = "recorded_model_probability"
    if pred.get("probability_kind") in {"vote_share", "support_share"} or basis in {"vote_share", "tipster_vote_share"}:
        kind = "vote_share_not_probability"
        reasons.append("vote_share_as_probability")
    if version == VOTE_POLICY and probability is not None:
        market_probability = _number(selected.get("market_probability"))
        if market_probability is None or not math.isclose(probability, market_probability, abs_tol=1e-6):
            reasons.append("vote_policy_probability_not_market_reference")
        kind = "market_reference" if market_probability is not None else kind
    warnings.append("probability_missing_roi_only" if raw_probability is None else "calibration_not_verified")
    if raw_probability is not None and not basis:
        warnings.append("probability_basis_not_saved")

    found, notes = _feature_findings(features, clocks["as_of"])
    reasons.extend(found)
    warnings.extend(notes)
    audit = _mapping(row.get("audit"))
    cutoff = features.get("feature_cutoff_at", audit.get("feature_cutoff_at"))
    if cutoff is None:
        warnings.append("source_feature_cutoff_not_saved_as_of_is_only_boundary")
    elif _time(cutoff) is None or (clocks["as_of"] and _time(cutoff) > clocks["as_of"]):
        reasons.append("invalid_source_feature_cutoff")
    input_hash = row.get("input_revision_hash")
    if not input_hash:
        warnings.append("input_revision_hash_not_saved")
    result = {
        "contract_version": CONTRACT_VERSION,
        **{key: row.get(key) for key in ("event_id", "snapshot_id", "decision_id", "ledger_sequence", "record_hash")},
        **{key: _stamp(row.get(key)) for key in clocks},
        "fixture": dict(fixture), "model_identity": identity,
        "model_metadata": dict(model),
        # A deterministic grouping key is NOT a saved artifact/code hash.
        "model_identity_hash": _hash(identity),
        "cohort_key": _hash(identity) if version else None,
        "operating": {"action": pred.get("action"), "selection_id": sid, "offer_id": oid,
                      "market": market, "label": label, "line": line,
                      "selection": selected.get("selection"), "odds": odds,
                      "probability": probability, "probability_kind": kind,
                      "probability_basis": basis, "calibration_status": "unverified"},
        "same_market_offers": deepcopy(roster), "all_offers": deepcopy(options),
        "input_revision_hash": input_hash,
        "batch_id": row.get("batch_id"), "common_input_hash": row.get("common_input_hash"),
        "source_feature_cutoff_at": _stamp(cutoff),
        "source_features": deepcopy(dict(features)),
        "model_inputs": deepcopy(features.get("model_inputs", features.get("score_inputs"))),
        "model_inputs_scope": "explicit" if "model_inputs" in features else "saved_score_inputs_only",
        "tipster_hybrid": deepcopy(dict(hybrid)),
        # Saved/replayed is caller-supplied evidence, never inferred or certified.
        "candidate_provenance": deepcopy(row.get("candidate_provenance")),
        "result_used": False, "hash_chain_verified": False,
        "warnings": sorted(set(warnings)), "exclusion_reasons": sorted(set(reasons)),
    }
    result["roi_input_eligible"] = not result["exclusion_reasons"]
    return result


def latest_pre_cutoff(records: Iterable[Mapping], cutoff_minutes: float = 30) -> dict:
    """Select once per event by (as_of, captured_at, ledger_sequence), then validate.

    All capture/as_of times must be strictly before kickoff minus the cutoff.
    Bad observed clocks or bad content on that chosen revision cannot cause an
    older revision to be substituted. Rejections and ignored settlements are
    returned separately. The iterable is consumed once and never mutated.
    """
    if _number(cutoff_minutes) is None or cutoff_minutes < 0:
        raise ValueError("cutoff_minutes must be finite and nonnegative")
    grouped, kickoff_sets = defaultdict(list), defaultdict(set)
    unorderable = set()
    rejected, ignored = [], Counter()
    for record in records:
        try:
            row = _unpack(record)
        except (TypeError, ValueError):
            rejected.append({"exclusion_reasons": ["invalid_record_json"]})
            continue
        if row.get("record_type") != "prediction":
            ignored[str(row.get("record_type", "unknown"))] += 1
            continue
        event = row.get("event_id")
        if not isinstance(event, str) or not event:
            rejected.append(normalize_operating_prediction(row))
            continue
        kickoff, as_of, captured = (_time(row.get(k)) for k in ("kickoff", "as_of", "captured_at"))
        if kickoff:
            kickoff_sets[event].add(kickoff)
        if not kickoff or not as_of or not captured:
            # A partially timed revision cannot be declared available at T30.
            rejected.append(normalize_operating_prediction(row))
            unorderable.add(event)
            continue
        boundary = kickoff - timedelta(minutes=cutoff_minutes)
        if max(as_of, captured) >= boundary:
            observation = normalize_operating_prediction(row)
            observation["exclusion_reasons"] = sorted(set(observation["exclusion_reasons"] + ["not_before_cutoff"]))
            observation["roi_input_eligible"] = False
            rejected.append(observation)
            continue
        sequence = row.get("ledger_sequence")
        sequence = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0
        grouped[event].append(((as_of, captured, sequence), row))
    selected = []
    for event in sorted(grouped):
        revisions = grouped[event]
        order = max(key for key, _ in revisions)
        tied = [row for key, row in revisions if key == order]
        observation = normalize_operating_prediction(tied[0])
        # Do not choose between conflicting ties using labels or input order.
        if any(row != tied[0] for row in tied[1:]):
            observation["exclusion_reasons"].append("ambiguous_latest_revision")
        if len(kickoff_sets[event]) != 1:
            observation["exclusion_reasons"].append("conflicting_event_kickoff")
        if event in unorderable:
            observation["exclusion_reasons"].append("unorderable_event_revision")
        observed = _time(observation.get("market_observed_at"))
        if observed and observed >= _time(observation["kickoff"]) - timedelta(minutes=cutoff_minutes):
            observation["exclusion_reasons"].append("market_not_before_cutoff")
        observation["exclusion_reasons"] = sorted(set(observation["exclusion_reasons"]))
        observation["roi_input_eligible"] = not observation["exclusion_reasons"]
        selected.append(observation)
    return {"contract_version": CONTRACT_VERSION, "cutoff_minutes": cutoff_minutes,
            "selected": selected, "rejected": rejected, "ignored_record_counts": dict(ignored),
            "selection_rule": "latest_available_revision_before_content_or_outcome_checks",
            "result_used": False, "hash_chain_verified": False}


__all__ = ["normalize_operating_prediction", "latest_pre_cutoff", "CONTRACT_VERSION"]
