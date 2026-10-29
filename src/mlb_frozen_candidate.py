"""Outcome-blind MLB research inference. No training, storage, network or clock.

The explicit loader is the only filesystem boundary. See the bridge document for
live versus retrospective time evidence; neither mode changes operating picks.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

VERSION = "mlb-frozen-linear-l2-10-v1"
CANDIDATE = "linear:l2=10"
BAT_FIELDS = ("plateAppearances", "strikeOuts", "baseOnBalls", "atBats", "hits",
              "hitByPitch", "sacFlies", "totalBases")
PIT_FIELDS = ("battersFaced", "strikeOuts", "baseOnBalls", "outs", "earnedRuns")
BAT_NAMES = ("xwoba", "xslg", "obp", "slg", "k", "bb", "missing_prior",
             "missing_current", "missing_savant")
PIT_NAMES = ("allowed_xwoba", "xera", "k", "bb", "kbb", "era", "current_reliability",
             "missing_prior", "missing_current", "missing_savant")
FEATURE_NAMES = tuple("lineup_" + n for n in BAT_NAMES) + tuple("starter_" + n for n in PIT_NAMES) + (
    "lineup_top3_xwoba", "lineup_bottom3_xwoba", "lineup_xwoba_spread", "lineup_weak_share")
# NumPy's original normalization sums these binary64 values to 9.000000000000002.
WEIGHTS = tuple(w / 9.000000000000002 for w in (1.12, 1.09, 1.06, 1.03, 1., .97, .94, .91, .88))
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "data/model_candidates/mlb"
MAX_ARTIFACT_BYTES = 2_000_000
FROZEN_HASHES = {
    "model.json": "1ec251d3c7265c606cc0f4dcb11a8c37568ba0f80a0b7594e41ca405f0f9f917",
    "priors-2024.json": "c9229570b4b27ee4c0616a95612c4e7b4af59b4b4b2a638b36b988be36c302bf",
    "priors-2025.json": "fd2f4b727c1aa68075ecf2cc134c07e60391f7e941a0812b7f61aca2a9ef7d21",
}


def canonical_hash(value):
    """Same canonical JSON convention as operating_ledger_inputs; never hash labels."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _time(value):
    try:
        t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _finite(value):
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _number(value, default=0.):
    return float(value) if _finite(value) else default


class Unavailable(ValueError):
    """A specific missing or invalid research input, never a request for fallback."""


def _require(condition, cause):
    if not condition:
        raise Unavailable(cause)


def _verify(envelope, kind):
    _require(isinstance(envelope, Mapping) and isinstance(envelope.get("payload"), Mapping),
             kind + ":invalid_artifact")
    payload = envelope["payload"]
    _require(canonical_hash(payload) == envelope.get("sha256"), kind + ":artifact_hash_mismatch")
    _require(payload.get("version") == VERSION, kind + ":unsupported_version")
    name = "model.json" if kind in ("model", "model.json") else f"priors-{payload.get('season')}.json"
    _require(envelope["sha256"] == FROZEN_HASHES.get(name), kind + ":not_frozen_artifact")
    return payload


@lru_cache(maxsize=2)
def _read_bundle(season):
    # One season only, after eligibility checks. No scan of experiment/source trees.
    manifest_path = ARTIFACT_DIR / "manifest.json"
    _require(manifest_path.stat().st_size <= 16_384, "artifact_manifest:oversize")
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    result = []
    for name in ("model.json", f"priors-{season - 1}.json"):
        path = ARTIFACT_DIR / name
        _require(path.stat().st_size <= MAX_ARTIFACT_BYTES, name + ":oversize")
        envelope = json.loads(path.read_text(encoding="utf8"))
        _verify(envelope, name)
        _require(envelope["sha256"] == manifest["artifacts"][name], name + ":manifest_hash_mismatch")
        result.append(envelope)
    return tuple(result)


def load_mlb_artifacts(season):
    """Explicit bounded local loader; returned JSON objects must be treated as read-only.

    Importing this module does not load artifacts or NumPy/research code. A caller
    can keep these envelopes and pass them into the pure API for an entire batch.
    """
    if season not in (2025, 2026):
        raise Unavailable("unsupported_season:only_2025_2026_frozen")
    # Copies protect the bounded cache from caller mutation.
    model, tables = _read_bundle(season)
    return json.loads(json.dumps(model)), json.loads(json.dumps(tables))


def player_features(player, kind, table):
    """Exact research EB denominators/strengths, including explicit zero-exposure flags."""
    pid = str(player["id"])
    prev, sv = table["stats"].get(pid, {}), table["savant"].get(pid, {})
    means = table["means"]
    exposure = "plateAppearances" if kind == "batter" else "battersFaced"
    n = _number(prev.get(exposure))
    prior_strength = 150. if kind == "batter" else 300.

    def rate(s, field, denom, default):
        return _number(s.get(field)) / denom if denom > 0 else default

    def eb(value, count, mean, strength=prior_strength):
        return (value * count + strength * mean) / (count + strength)

    prior_k = eb(rate(prev, "strikeOuts", n, means["k"]), n, means["k"])
    prior_bb = eb(rate(prev, "baseOnBalls", n, means["bb"]), n, means["bb"])
    svpa = _number(sv.get("pa"))
    xwoba = eb(_number(sv.get("est_woba"), means["xwoba"]), svpa, means["xwoba"])
    xslg = eb(_number(sv.get("est_slg"), means["xslg"]), svpa, means["xslg"])
    xera = eb(_number(sv.get("xera"), means["xera"]), svpa, means["xera"])
    current = player["batting" if kind == "batter" else "pitching"]
    curr_n = _number(current.get(exposure))
    strength = 150. if kind == "batter" else 100.
    k = eb(rate(current, "strikeOuts", curr_n, prior_k), curr_n, prior_k, strength)
    bb = eb(rate(current, "baseOnBalls", curr_n, prior_bb), curr_n, prior_bb, strength)
    missing = [float(n == 0), float(curr_n == 0), float(not sv or svpa == 0)]
    if kind == "batter":
        def obp(s):
            denom = sum(_number(s.get(f)) for f in ("atBats", "baseOnBalls", "hitByPitch", "sacFlies"))
            numer = sum(_number(s.get(f)) for f in ("hits", "baseOnBalls", "hitByPitch"))
            return numer / denom if denom else means["obp"], denom
        pr_obp, pn = obp(prev)
        pr_obp = eb(pr_obp, pn, means["obp"])
        pr_ab = _number(prev.get("atBats"))
        pr_slg = eb(rate(prev, "totalBases", pr_ab, means["slg"]), pr_ab, means["slg"])
        cu_obp, cn = obp(current)
        cu_ab = _number(current.get("atBats"))
        return [xwoba, xslg, eb(cu_obp, cn, pr_obp, 150.),
                eb(rate(current, "totalBases", cu_ab, pr_slg), cu_ab, pr_slg, 150.), k, bb] + missing
    pr_outs = _number(prev.get("outs"))
    pr_era = eb(rate(prev, "earnedRuns", pr_outs / 27, means["era"]), n, means["era"], 300.)
    cu_outs = _number(current.get("outs"))
    era = eb(rate(current, "earnedRuns", cu_outs / 27, pr_era), cu_outs / 3, pr_era, 30.)
    return [xwoba, xera, k, bb, k - bb, era, curr_n / (curr_n + 100.)] + missing


def feature_row(snapshot, tables):
    """Pure feature engine for a validated research-shaped pregame snapshot.

    Uses every frozen previous-season player table, including population means;
    unknown prior players use the frozen EB missing-prior semantics, never a
    substitute lineup. Only the primary's 23 linear features are constructed.
    """
    _validate_sides(snapshot)
    values = {}
    for side in ("home", "away"):
        source = snapshot["sides"][side]
        batters = [player_features(p, "batter", tables["batter"]) for p in source["lineup"]]
        lineup = [sum(w * b[j] for w, b in zip(WEIGHTS, batters)) for j in range(len(BAT_NAMES))]
        spread = math.sqrt(sum(w * (b[0] - lineup[0]) ** 2 for w, b in zip(WEIGHTS, batters)))
        extras = [sum(b[0] for b in batters[:3]) / 3, sum(b[0] for b in batters[6:]) / 3,
                  spread, sum(w for w, b in zip(WEIGHTS, batters) if b[0] < .300)]
        values[side] = lineup + player_features(source["starter"], "pitcher", tables["pitcher"]) + extras
    return [h - a for h, a in zip(values["home"], values["away"])]


def predict_features(features, stage_model):
    """Fixed-coefficient math only; no labels or model selection; abs parity 1e-12."""
    _require(len(features) == len(FEATURE_NAMES), "features:expected_23")
    _require(all(_finite(v) for v in features), "features:nonfinite")
    z = [max(-6., min(6., (float(x) - c) / s))
         for x, c, s in zip(features, stage_model["center"], stage_model["scale"])]
    logit = stage_model["beta"][0] + sum(x * b for x, b in zip(z, stage_model["beta"][1:]))
    return 1 / (1 + math.exp(-max(-40., min(40., logit))))


def _validate_sides(snapshot):
    sides = snapshot.get("sides")
    _require(isinstance(sides, Mapping), "sides:required_mapping")
    for side in ("home", "away"):
        source = sides.get(side)
        _require(isinstance(source, Mapping), side + ":required_mapping")
        lineup = source.get("lineup", [])
        _require(isinstance(lineup, list) and len(lineup) == 9, side + ":nine_announced_starters_required")
        ids = [p.get("id") for p in lineup if isinstance(p, Mapping)]
        _require(len(ids) == 9 and all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids)
                 and len(set(ids)) == 9, side + ":invalid_or_duplicate_lineup_ids")
        starter = source.get("starter", {})
        _require(isinstance(starter, Mapping) and isinstance(starter.get("id"), int) and
                 not isinstance(starter["id"], bool) and starter["id"] > 0, side + ":starting_pitcher_required")
        for player, kind, fields in [(p, "batting", BAT_FIELDS) for p in lineup] + [(starter, "pitching", PIT_FIELDS)]:
            stats = player.get(kind)
            _require(isinstance(stats, Mapping), f"{side}:{player['id']}:{kind}:seasonStats_required")
            for field in fields:
                _require(field in stats and _finite(stats[field]) and float(stats[field]) >= 0,
                         f"{side}:{player['id']}:{kind}:{field}:missing_or_invalid")
    home_ids = {p["id"] for p in sides["home"]["lineup"]} | {sides["home"]["starter"]["id"]}
    away_ids = {p["id"] for p in sides["away"]["lineup"]} | {sides["away"]["starter"]["id"]}
    _require(not home_ids.intersection(away_ids), "sides:opponents_share_player_identity")


def _validate(snapshot, decision_at, matchup, odds):
    _require(isinstance(snapshot, Mapping) and isinstance(matchup, Mapping) and isinstance(odds, Mapping),
             "input:expected_mappings")
    _require(matchup.get("league") == "MLB", "unsupported_league")
    if "start_at" in matchup:
        _require(_time(matchup["start_at"]) is not None and _time(matchup["start_at"]) == _time(snapshot.get("start_at")),
                 "matchup:start_at_mismatch")
    mode = snapshot.get("source_kind")
    collector = mode == "official_live_completed_http"
    historical = mode == "official_historical_timecode_replay"
    _require(mode in ("official_live_pregame", "official_live_completed_http", "official_historical_timecode_replay"),
             "source_kind:unsupported")
    _require(snapshot.get("gamePk") == matchup.get("gamePk") and
             isinstance(matchup.get("gamePk"), int) and not isinstance(matchup["gamePk"], bool), "matchup:gamePk_mismatch")
    for side in ("home", "away"):
        _require(bool(matchup.get(side)) and (snapshot.get(side) == matchup[side] or
                 (side not in snapshot and (collector or historical))), "matchup:" + side + "_mismatch")
    _require(matchup["home"] != matchup["away"], "matchup:identical_teams")
    decision = _time(decision_at)
    _require(decision is not None, "decision_at:aware_time_required")
    clocks = {}
    for field in ("start_at", "snapshot_at", "collected_at"):
        clocks[field] = _time(snapshot.get("observed_at") if collector and field == "collected_at" else snapshot.get(field))
        _require(clocks[field] is not None, field + ":aware_time_required")
    start = clocks["start_at"]
    if not historical or "feed_start_at" in snapshot:
        clocks["feed_start_at"] = _time(snapshot.get("feed_start_at"))
        _require(clocks["feed_start_at"] is not None, "feed_start_at:aware_time_required")
        _require(start == clocks["feed_start_at"], "feed_start_at:matchup_start_mismatch")
    _require(clocks["snapshot_at"] <= decision < start, "snapshot_at:not_pregame_at_decision")
    if not historical:
        _require(clocks["snapshot_at"] <= clocks["collected_at"] <= decision, "collected_at:after_decision_or_before_snapshot")
        _require(decision <= start - timedelta(minutes=30), "decision_at:after_live_T30")
    else:
        _require(clocks["collected_at"] >= clocks["snapshot_at"], "collected_at:before_snapshot")
        _require(snapshot.get("accepted") is True, "historical:archive_acceptance_required")
        cutoff = _time(snapshot.get("cutoff_at"))
        _require(cutoff is not None and clocks["snapshot_at"] <= cutoff <= decision,
                 "historical:invalid_cutoff_proof")
    if collector:
        _require(snapshot.get("version") == "mlb-candidate-feed-v1" and snapshot.get("status") == "captured",
                 "collector:validated_capture_required")
    _require(snapshot.get("starter_identity_kind", "official_published_probable_pitcher") == "official_published_probable_pitcher"
             and snapshot.get("starter_final_confirmed", False) is False, "starter:unsupported_identity_claim")
    _require(snapshot.get("lineup_identity_kind", "official_boxscore_batting_order") == "official_boxscore_batting_order",
             "lineup:unsupported_identity_claim")
    status = snapshot.get("source_status") if collector else snapshot.get("status")
    _require(status in ("Scheduled", "Pre-Game", "Warmup", "Delayed Start"), "status:not_pregame")
    if not collector and not historical:
        _require(snapshot.get("no_actual_plays") is True, "actual_plays:absence_proof_required")
        _require(snapshot.get("lineups_announced") is True, "lineup:announcement_proof_required")
    url = snapshot.get("source_url") if collector else snapshot.get("url")
    _require(isinstance(url, str) and url.startswith("https://statsapi.mlb.com/"),
             "source:official_url_required")
    digest = snapshot.get("response_sha256")
    _require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
             "source:response_sha256_required")
    _validate_sides(snapshot)
    for side in ("home", "away"):
        _require(_finite(odds.get(side)) and float(odds[side]) > 1, "odds:" + side + ":invalid_decimal_price")
    observed = _time(odds.get("observed_at"))
    unverified_quote = historical and odds.get("observed_at") is None and odds.get("time_scope") == "archived_unverified_time"
    _require(unverified_quote or (observed is not None and observed <= decision), "odds:observed_at_missing_or_after_decision")
    _require(bool(odds.get("source")) and bool(odds.get("snapshot_id")), "odds:provenance_required")
    _require(start.year in (2025, 2026), "unsupported_season:only_2025_2026_frozen")
    return start, decision, clocks


def infer_mlb_candidate(snapshot, *, decision_at, matchup, odds, model, prior_tables):
    """Pure, outcome-blind inference with explicit artifact envelopes.

    Typical bad observations return unavailable with an exact cause, no market
    fallback. Unknown extra fields (including saved results) never enter features
    or output hashes. Historical acceptance is retrospective source evidence,
    explicitly not proof of contemporaneous live collection or a T30 decision.
    """
    base = dict(version=VERSION, candidate=CANDIDATE, research_only=True, result_used=False,
                operating_model_reproduced=False, affects_operating_selection=False,
                affects_operating_probability=False, validated_edge=False)
    try:
        start, decision, clocks = _validate(snapshot, decision_at, matchup, odds)
        artifact, priors = _verify(model, "model"), _verify(prior_tables, "priors")
        _require(artifact.get("candidate") == CANDIDATE, "model:wrong_frozen_candidate")
        _require(priors.get("season") == start.year - 1, "priors:wrong_season")
        stage = "before2025" if start.year == 2025 else "before2026"
        frozen = artifact["models"][stage]
        _require(_time(frozen["training_cutoff_at"]) < decision, "model:training_cutoff_not_before_decision")
        _require(tuple(frozen["feature_names"]) == FEATURE_NAMES and frozen["family"] == "linear" and frozen["ridge"] == 10.,
                 "model:feature_contract_mismatch")
        features = feature_row(snapshot, priors["tables"])
        p = predict_features(features, frozen)
        pick = "home" if p >= .5 else "away"
        # Project precisely the consumed shape. Do not hash labels/unknown fields.
        sides = {}
        for side in ("home", "away"):
            source = snapshot["sides"][side]
            sides[side] = {"lineup": [{"id": player["id"], "batting": {k: player["batting"][k] for k in BAT_FIELDS}}
                                     for player in source["lineup"]],
                           "starter": {"id": source["starter"]["id"], "pitching": {
                               k: source["starter"]["pitching"][k] for k in PIT_FIELDS}}}
        collector = snapshot["source_kind"] == "official_live_completed_http"
        historical = snapshot["source_kind"] == "official_historical_timecode_replay"
        evidence = dict(gamePk=matchup["gamePk"], league="MLB", home=matchup["home"], away=matchup["away"],
                        decision_at=decision.isoformat(), **{k: v.isoformat() for k, v in clocks.items()},
                        source_kind=snapshot["source_kind"], url=snapshot["source_url"] if collector else snapshot["url"],
                        response_sha256=snapshot["response_sha256"],
                        status=snapshot["source_status"] if collector else snapshot["status"], sides=sides,
                        odds={k: odds[k] for k in ("home", "away", "observed_at", "source", "snapshot_id")})
        # Datetime callers are supported at the boundary; normalize hashes too.
        observed = _time(odds["observed_at"])
        evidence["odds"]["observed_at"] = observed.isoformat() if observed else None
        evidence["odds"]["time_scope"] = odds.get("time_scope")
        evidence["pregame_gate"] = ("collector_v1_S_P_no_pitch_nine_battingOrder" if collector else
                                    "archive_accepted_S_P_no_pitch_nine_battingOrder" if historical else
                                    "explicit_no_actual_plays_and_announced_lineups")
        evidence.update(starter_identity_kind="official_published_probable_pitcher", starter_final_confirmed=False,
                        lineup_identity_kind="official_boxscore_batting_order")
        if "cutoff_at" in snapshot and snapshot["source_kind"] == "official_historical_timecode_replay":
            evidence["cutoff_at"] = _time(snapshot["cutoff_at"]).isoformat()
        return dict(base, status="available", reasons=[], probability_home=p, probability_away=1-p,
                    selection=pick, selected_odds=odds[pick], feature_names=list(FEATURE_NAMES), features=features,
                    model_stage=stage, training_cutoff_at=frozen["training_cutoff_at"],
                    model_artifact_hash=model["sha256"], prior_artifact_hash=prior_tables["sha256"],
                    input_hash=canonical_hash(evidence), feature_hash=canonical_hash(features),
                    evidence=evidence, odds_time_verified=observed is not None,
                    feed_start_verified="feed_start_at" in clocks,
                    starter_identity_kind="official_published_probable_pitcher", starter_final_confirmed=False,
                    lineup_identity_kind="official_boxscore_batting_order",
                    time_evidence=("retrospective_timecode_not_live_capture" if historical else "contemporaneous_live_T30"))
    except (Unavailable, KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        return dict(base, status="unavailable", reasons=[str(exc) if isinstance(exc, Unavailable) else
                    "invalid_input_or_artifact:" + type(exc).__name__], probability_home=None,
                    probability_away=None, selection=None, selected_odds=None)


def infer_mlb_candidate_local(snapshot, *, decision_at, matchup, odds):
    """Optional local-loader wrapper, still no network/DB/clock/environment access."""
    try:
        start, _, _ = _validate(snapshot, decision_at, matchup, odds)
        model, priors = load_mlb_artifacts(start.year)
    except (Unavailable, OSError, KeyError, TypeError, ValueError) as exc:
        result = infer_mlb_candidate(snapshot, decision_at=decision_at, matchup=matchup, odds=odds,
                                     model={}, prior_tables={})
        if result["reasons"] == ["model:invalid_artifact"]:
            result["reasons"] = [str(exc) if isinstance(exc, Unavailable) else "artifact_load:" + type(exc).__name__]
        return result
    return infer_mlb_candidate(snapshot, decision_at=decision_at, matchup=matchup, odds=odds,
                               model=model, prior_tables=priors)
