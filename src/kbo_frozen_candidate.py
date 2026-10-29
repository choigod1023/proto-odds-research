"""Bounded, research-only inference of the frozen KBO e0 residual candidate.

No training, DB, network, environment or wall clock. Static artifacts are read
once on import. Caller attestations are provenance, not independently verified
proof of complete official history. See the bridge report for the wire contract.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

VERSION = "kbo-frozen-e0-bridge-v1"
CANDIDATE = "e0:residual:0.03"
TEAMS = tuple(sorted(("LG", "KT", "삼성", "한화", "SSG", "키움", "두산", "NC", "KIA", "롯데")))
KST = timezone(timedelta(hours=9))
MAX_RESULTS = 12000
MAX_STALE_DAYS = 30
ORIGIN = "2023-04-01"
ARCHIVE_POLICY = "historical_prior_day_midnight_kst_assumption"
_DIRECTORY = Path(__file__).resolve().parents[1] / "data/model_candidates/kbo"


def signature(value):
    """Canonical SHA256 for JSON-safe caller inputs, without reading a file."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


_MODEL = json.loads((_DIRECTORY / "frozen-e0.json").read_text(encoding="utf8"))
_SEED = json.loads((_DIRECTORY / "end-history-seed.json").read_text(encoding="utf8"))
MODEL_HASH = signature(_MODEL)
SEED_HASH = signature(_SEED)


def artifact():
    return deepcopy(_MODEL)


def historical_seed():
    """End-of-day state, NOT contemporary availability evidence or live state."""
    return deepcopy(_SEED)


def _fail(reason):
    raise ValueError(reason)


def _require(condition, reason):
    if not condition:
        _fail(reason)


def _time(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(stamp.tzinfo is not None and stamp.utcoffset() is not None, "aware_timestamp_required")
        return stamp.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        _fail("aware_timestamp_required")


def _day(value):
    _require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "invalid_date")
    return date.fromisoformat(value)


def _number(value):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), "invalid_number")
    return float(value)


def _fixture(fixture):
    _require(fixture["league"] == "KBO", "unsupported_league")
    _day(fixture["date"])
    _require(fixture["home"] in TEAMS and fixture["away"] in TEAMS and fixture["home"] != fixture["away"], "unsupported_team")
    return {k: fixture[k] for k in ("league", "date", "home", "away")}


def _envelope(status="unavailable", reason=None, **fields):
    return dict(version=VERSION, candidate=CANDIDATE, artifact_hash=MODEL_HASH,
                status=status, reason=reason, affects_selection=False,
                affects_probability=False, promoted=False, **fields)


def _unavailable(exc):
    return _envelope(reason="missing_field:" + str(exc.args[0]) if isinstance(exc, KeyError) else str(exc))


def _sigmoid(z):
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def _probability(x, reference, fit):
    _require(0 < reference < 1, "invalid_reference_probability")
    residual = max(-6., min(6., x / fit["scale"][0])) * fit["beta"][0]
    return _sigmoid(math.log(reference / (1 - reference)) + residual)


class _E0State:
    """One league, ten teams, day-keyed common prior and accumulated gradients."""
    def __init__(self, seed=None):
        self.mean = dict.fromkeys(TEAMS, 0.)
        self.counts = dict.fromkeys(TEAMS, 0)
        self.team_latest = dict.fromkeys(TEAMS)
        self.day = self.latest = None
        self.total = 0
        if seed is not None:
            self.mean.update(seed["mean"])
            self.counts.update(seed["counts"])
            self.team_latest.update(seed["team_latest"])
            self.day, self.latest, self.total = seed["through_date"], seed["latest_observation"], seed["total_observations"]

    def advance(self, day):
        if self.day is not None:
            days = (_day(day) - _day(self.day)).days
            _require(days >= 0, "time_reversal")
            factor = 2 ** (-days / 120) * .35 ** (int(day[:4]) - int(self.day[:4]))
            for team in TEAMS:
                self.mean[team] *= factor
        self.day = day

    def observe(self, home, away):
        x = self.mean[home] - self.mean[away]
        return dict(x=[x], state_logit=.12 + x, home_state=[self.mean[home]],
                    away_state=[self.mean[away]], home_games=self.counts[home],
                    away_games=self.counts[away], latest_observation=self.latest,
                    total_observations=self.total)

    def update(self, games):
        grad = dict.fromkeys(TEAMS, 0.)
        ids = set()
        for row in sorted(games, key=lambda r: r["source_fixture_id"]):
            _require(row["source_fixture_id"] not in ids, "duplicate_update_fixture")
            ids.add(row["source_fixture_id"])
            _require(row["date"] == self.day, "update_wrong_day")
            h, a, hg, ag = row["home"], row["away"], row["hg"], row["ag"]
            y = .5 if hg == ag else float(hg > ag)
            weight = min(2., math.log1p(abs(hg - ag)) / math.log(4)) if y != .5 else 1.
            delta = .08 * weight * (y - _sigmoid(.12 + self.mean[h] - self.mean[a]))
            grad[h] += delta
            grad[a] -= delta
            for team in (h, a):
                self.counts[team] += 1
                self.team_latest[team] = self.day
        for team in TEAMS:
            self.mean[team] += grad[team]
        if games:
            self.latest = self.day
            self.total += len(games)

    def seed(self):
        return dict(through_date=self.day, mean=dict(self.mean), counts=dict(self.counts),
                    team_latest=dict(self.team_latest), latest_observation=self.latest,
                    total_observations=self.total)


def normalize_proto_history(rows, *, decision_at, aliases=None):
    """Resolve match_results revisions BEFORE validating confirmed final scores.

    Only this adapter interprets documented naive match_results kickoff as KST.
    Explicit aliases extend canonical team names; ambiguous/unknown names fail.
    source='dataset' is never assigned historical availability. A future
    correction is ignored; an available unconfirmed correction invalidates.
    """
    try:
        decision = _time(decision_at)
        names = {t: t for t in TEAMS}
        names.update(aliases or {})
        latest, scanned, ignored = {}, 0, 0
        for row in rows:
            scanned += 1
            _require(scanned <= MAX_RESULTS, "result_limit_exceeded")
            _require(row["source"] == "proto", "unsupported_result_source")
            observed = _time(row["observed_at"])
            if observed >= decision:
                ignored += 1
                continue
            identity = [row[k] for k in ("source", "season", "round", "game_no")]
            _require(all(v is not None and str(v) for v in identity), "missing_result_identity")
            key = signature(identity)
            if key not in latest or observed > latest[key][0]:
                latest[key] = (observed, row, False)
            elif observed == latest[key][0] and row != latest[key][1]:
                latest[key] = (observed, row, True)
        output, physical = [], set()
        for key, (observed, row, conflict) in sorted(latest.items()):
            _require(not conflict, "conflicting_equal_time_result")
            _require(row["league"] == "KBO", "unsupported_league")
            _require(row["result_state"] == "confirmed", "latest_result_not_confirmed")
            kickoff = datetime.fromisoformat(row["kickoff"])
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=KST)
            _require(observed > kickoff, "final_observed_not_after_kickoff")
            h, a = names.get(row["home_team"]), names.get(row["away_team"])
            _require(h in TEAMS and a in TEAMS and h != a, "unsupported_team")
            fixture_key = (kickoff.astimezone(timezone.utc), h, a)
            _require(fixture_key not in physical, "duplicate_physical_fixture")
            physical.add(fixture_key)
            scores = [row["home_score"], row["away_score"]]
            _require(all(_number(s) >= 0 and int(s) == s for s in scores), "invalid_final_score")
            output.append(dict(source_fixture_id="proto:" + key, league="KBO",
                               date=kickoff.astimezone(KST).date().isoformat(), home=h, away=a,
                               hg=scores[0], ag=scores[1], available_at=observed.isoformat(),
                               availability_basis="official_final_observed_at"))
        output.sort(key=lambda r: (r["date"], r["source_fixture_id"]))
        return _envelope("available", records=output, records_hash=signature(output),
                         ignored_future_revisions=ignored)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)


def verify_increment_coverage(*, schedule_days, results, coverage_from, complete_through, as_of, source_id):
    """Reconcile explicit official daily manifests, including zero-game dates.

    Each day: date, league='KBO', source='kbo_official_calendar', complete=True,
    observed_at (aware actual receipt), source_sha256 (raw response hash), games.
    Each game: source_fixture_id, home, away, status=final/cancelled/postponed;
    final games also carry hg/ag from the official calendar for score crosscheck.
    IDs must already have an unambiguous official/proto crosswalk. Results use
    the normalized build_daily_state schema. No guessing from missing games,
    scrape dates, team pairs or absent calendar responses. Raw source truth and
    parser completeness remain the collecting job's responsibility.
    """
    try:
        start, end, decision = _day(coverage_from), _day(complete_through), _time(as_of)
        _require(source_id and 0 <= (end - start).days < 3660, "invalid_coverage_interval")
        _require(end <= decision.astimezone(KST).date(), "coverage_day_not_observable")
        indexed, records = {}, []
        for row in results:
            _require(len(records) < MAX_RESULTS, "result_limit_exceeded")
            record = {k: row[k] for k in ("source_fixture_id", "league", "date", "home", "away", "hg", "ag", "available_at", "availability_basis")}
            _fixture(record)
            _require(start <= _day(record["date"]) <= end, "result_outside_history_window")
            _require(record["source_fixture_id"] not in indexed, "duplicate_result_identity")
            _require(record["availability_basis"] == "official_final_observed_at", "unproven_result_availability")
            observed = _time(record["available_at"])
            _require(observed <= decision, "result_not_available_by_as_of")
            _require(observed.astimezone(KST).date() >= _day(record["date"]), "result_observation_before_game_date")
            _require(all(_number(record[k]) >= 0 and int(record[k]) == record[k] for k in ("hg", "ag")), "invalid_final_score")
            indexed[record["source_fixture_id"]] = record
            records.append(record)
        days, seen_games, expected, manifests = set(), set(), set(), []
        for manifest in schedule_days:
            _require(len(manifests) < 3660, "schedule_limit_exceeded")
            day = manifest["date"]
            _require(start <= _day(day) <= end and day not in days, "duplicate_or_outside_schedule_day")
            days.add(day)
            _require(manifest["league"] == "KBO" and manifest["source"] == "kbo_official_calendar", "unsupported_schedule_source")
            _require(manifest["complete"] is True, "schedule_day_not_complete")
            stamp = _time(manifest["observed_at"])
            _require(_day(day) <= stamp.astimezone(KST).date() and stamp <= decision, "schedule_observation_outside_window")
            digest = manifest["source_sha256"]
            _require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "missing_schedule_source_hash")
            _require(isinstance(manifest["games"], (list, tuple)) and len(manifest["games"]) <= 20, "schedule_game_limit_exceeded")
            games = []
            for game in manifest["games"]:
                _fixture(dict(game, league="KBO", date=day))
                gid = game["source_fixture_id"]
                _require(isinstance(gid, str) and gid and gid not in seen_games, "duplicate_or_missing_schedule_game_id")
                seen_games.add(gid)
                status = game["status"]
                _require(status in ("final", "cancelled", "postponed"), "schedule_game_not_terminal:" + gid)
                if status == "final":
                    _require(gid in indexed, "missing_final_result:" + gid)
                    result = indexed[gid]
                    _require((result["date"], result["home"], result["away"]) == (day, game["home"], game["away"]), "schedule_result_fixture_mismatch:" + gid)
                    _require((result["hg"], result["ag"]) == (game["hg"], game["ag"]), "schedule_result_score_mismatch:" + gid)
                    expected.add(gid)
                else:
                    _require(gid not in indexed, "result_for_unplayed_schedule_game:" + gid)
                games.append({k: game[k] for k in ("source_fixture_id", "home", "away", "status")} |
                             ({"hg": game["hg"], "ag": game["ag"]} if status == "final" else {}))
            manifests.append(dict(date=day, league="KBO", source=manifest["source"], complete=True,
                                  observed_at=stamp.isoformat(), source_sha256=digest,
                                  games=sorted(games, key=lambda r: r["source_fixture_id"])))
        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            _require(day in days, "missing_schedule_day:" + day)
        _require(set(indexed) == expected, "unexpected_result_outside_schedule")
        records.sort(key=lambda r: (r["date"], r["source_fixture_id"]))
        payload = dict(source_id=source_id, coverage_from=coverage_from, complete_through=complete_through,
                       as_of=decision.isoformat(), manifests=sorted(manifests, key=lambda r: r["date"]),
                       records_hash=signature(records))
        evidence_hash = signature(payload)
        source = dict(source_id=source_id, records_hash=payload["records_hash"], coverage_from=coverage_from,
                      complete_through=complete_through, coverage_verified=True,
                      coverage_available_at=decision.isoformat(), coverage_evidence_hash=evidence_hash)
        return _envelope("available", source_signature=source, coverage_evidence=payload,
                         source_authenticity="caller_supplied_not_independently_authenticated")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)


def build_daily_state(*, state_date, as_of, results, source_signature, seed=None):
    """Build the exact e0 day prior from explicit final observations, fail closed.

    results is a bounded iterable of normalized records (see adapter). Signature
    must bind records_hash, coverage_from, complete_through, source_id, plus
    coverage_verified=True and coverage_evidence_hash from an external audit. Without
    seed, full coverage begins 2023-04-01. With the bundled seed, also require
    seed_hash, seed_observed_at and seed_history_unchanged=True. Coverage is a
    caller attestation; this module cannot discover missing official games.
    """
    try:
        day, decision = state_date, _time(as_of)
        _day(day)
        _require(decision.astimezone(KST).date().isoformat() <= day, "decision_date_mismatch")
        _require(source_signature["source_id"], "missing_source_id")
        _require(source_signature.get("coverage_verified") is True, "history_completeness_unproven")
        evidence = source_signature.get("coverage_evidence_hash", "")
        _require(isinstance(evidence, str) and len(evidence) == 64 and all(c in "0123456789abcdef" for c in evidence), "missing_coverage_evidence_hash")
        previous = (_day(day) - timedelta(days=1)).isoformat()
        _require(source_signature["complete_through"] == previous, "history_coverage_not_previous_day")
        coverage_seen = _time(source_signature["coverage_available_at"])
        _require(previous <= coverage_seen.astimezone(KST).date().isoformat() and coverage_seen <= decision,
                 "coverage_not_available_by_as_of")
        dependency_times = [coverage_seen]
        state, records = _E0State(), []
        start = ORIGIN
        if seed is not None:
            _require(signature(seed) == SEED_HASH and source_signature["seed_hash"] == SEED_HASH, "unsupported_seed")
            seed_seen = _time(source_signature["seed_observed_at"])
            _require(seed_seen <= decision, "seed_not_available_by_as_of")
            end_of_seed = datetime.combine(_day(seed["through_date"]) + timedelta(days=1), datetime.min.time(), KST)
            _require(seed_seen >= end_of_seed, "seed_observation_before_history_end")
            _require(source_signature["seed_history_unchanged"] is True, "seed_correction_requires_full_rebuild")
            _require(seed["through_date"] < day, "seed_not_before_target_date")
            state = _E0State(seed)
            dependency_times.append(seed_seen)
            start = (_day(seed["through_date"]) + timedelta(days=1)).isoformat()
        _require(source_signature["coverage_from"] == start, "history_origin_not_proven")
        ids = set()
        for row in results:
            _require(len(records) < MAX_RESULTS, "result_limit_exceeded")
            record = {k: row[k] for k in ("source_fixture_id", "league", "date", "home", "away", "hg", "ag", "available_at", "availability_basis")}
            _fixture(record)
            _require(start <= record["date"] < day, "result_outside_history_window")
            _require(record["source_fixture_id"] not in ids, "duplicate_result_identity")
            ids.add(record["source_fixture_id"])
            _require(record["availability_basis"] == "official_final_observed_at", "unproven_result_availability")
            seen = _time(record["available_at"])
            _require(seen <= decision, "result_not_available_by_as_of")
            _require(seen.astimezone(KST).date().isoformat() >= record["date"], "result_observation_before_game_date")
            _require(all(_number(record[k]) >= 0 and int(record[k]) == record[k] for k in ("hg", "ag")), "invalid_final_score")
            records.append(record)
            dependency_times.append(seen)
        records.sort(key=lambda r: (r["date"], r["source_fixture_id"]))
        _require(signature(records) == source_signature["records_hash"], "result_signature_mismatch")
        groups = defaultdict(list)
        for row in records:
            groups[row["date"]].append(row)
        for observed_day in sorted(groups):
            state.advance(observed_day)
            state.update(groups[observed_day])
        state.advance(day)
        payload = dict(league="KBO", state_date=day, available_at=decision.isoformat(), e0=state.seed(),
                       latest_input_available_at=max(dependency_times).isoformat(),
                       source_signature=deepcopy(source_signature),
                       availability_basis="explicit_observation_with_caller_attested_coverage",
                       seed_historical_availability="unproven" if seed else None)
        return _envelope("available", **payload, daily_state_hash=signature(payload))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)


def state_for_fixture(*, daily_state, fixture, decision_at):
    """O(ten teams) binding of a daily cache to a decision for that target date.

    No history scan or artifact IO. Cache available_at must be <= decision_at;
    corrections require the collecting job to replace/invalidate its cache.
    """
    try:
        target, decision = _fixture(fixture), _time(decision_at)
        _require(daily_state["status"] == "available", "daily_state_unavailable:" + str(daily_state.get("reason")))
        _require(daily_state["version"] == VERSION and daily_state["artifact_hash"] == MODEL_HASH, "state_model_mismatch")
        keys = ("league", "state_date", "available_at", "latest_input_available_at", "e0", "source_signature", "availability_basis", "seed_historical_availability")
        _require(daily_state["daily_state_hash"] == signature({k: daily_state[k] for k in keys}), "daily_state_signature_mismatch")
        day = target["date"]
        _require(daily_state["league"] == "KBO" and daily_state["state_date"] == day, "daily_state_date_or_league_mismatch")
        _require(decision.astimezone(KST).date().isoformat() <= day, "decision_date_mismatch")
        _require(_time(daily_state["available_at"]) <= decision, "daily_state_after_decision")
        _require(_time(daily_state["latest_input_available_at"]) < decision, "input_not_available_before_decision")
        state = _E0State(daily_state["e0"])
        _require(state.day == day and state.latest is not None and state.latest < day, "invalid_daily_state_prior")
        for team in (target["home"], target["away"]):
            latest = state.team_latest[team]
            _require(latest is not None and latest[:4] == day[:4], "missing_current_season_team_history")
            _require((_day(day) - _day(latest)).days <= MAX_STALE_DAYS, "stale_team_state")
        payload = dict(fixture=target, decision_at=decision.isoformat(), feature=state.observe(target["home"], target["away"]),
                       source_signature=deepcopy(daily_state["source_signature"]),
                       daily_state_hash=daily_state["daily_state_hash"],
                       availability_basis=daily_state["availability_basis"],
                       seed_historical_availability=daily_state["seed_historical_availability"])
        return _envelope("available", **payload, state_hash=signature(payload))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)


def build_state(*, fixture, decision_at, results, source_signature, seed=None):
    """Convenience full reconstruction; collectors should cache build_daily_state."""
    try:
        target = _fixture(fixture)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return _unavailable(exc)
    daily = build_daily_state(state_date=target["date"], as_of=decision_at, results=results,
                             source_signature=source_signature, seed=seed)
    return state_for_fixture(daily_state=daily, fixture=fixture, decision_at=decision_at) if daily["status"] == "available" else daily


def infer_candidate(*, fixture, decision_at, state, reference, offers):
    """Future same-snapshot API; final_fit ONLY, never changes operating picks.

    reference: recipe=fixed91_same_electorate, home_vote_share, voters,
    observed_at, snapshot_id. Two offers: side, selection_id, offer_id, odds,
    market_probability, observed_at, snapshot_id, market='승패', line=None.
    fixture includes aware kickoff; all observations <= decision < kickoff.
    Caller remains responsible for electorate eligibility and outer T-30 audit.
    """
    try:
        target, decision = _fixture(fixture), _time(decision_at)
        _require(decision < _time(fixture["kickoff"]), "decision_not_before_kickoff")
        _require(_time(fixture["kickoff"]).astimezone(KST).date().isoformat() == target["date"], "kickoff_date_mismatch")
        _require(state["status"] == "available", "state_unavailable:" + str(state.get("reason")))
        _require(state["version"] == VERSION and state["artifact_hash"] == MODEL_HASH, "state_model_mismatch")
        keys = ("fixture", "decision_at", "feature", "source_signature", "daily_state_hash", "availability_basis", "seed_historical_availability")
        _require(state["state_hash"] == signature({k: state[k] for k in keys}), "state_signature_mismatch")
        _require(state["fixture"] == target and _time(state["decision_at"]) == decision, "state_fixture_or_time_mismatch")
        _require(reference["recipe"] == "fixed91_same_electorate", "unsupported_reference_recipe")
        voters = _number(reference["voters"])
        _require(voters > 0 and int(voters) == voters, "missing_eligible_votes")
        vote = _number(reference["home_vote_share"])
        _require(0 <= vote <= 1, "invalid_vote_share")
        _require(_time(reference["observed_at"]) <= decision, "reference_after_decision")
        _require(isinstance(offers, (list, tuple)) and len(offers) == 2, "two_way_offers_required")
        sides = {o["side"]: o for o in offers}
        _require(set(sides) == {"home", "away"}, "two_way_offers_required")
        _require(reference["snapshot_id"] and all(o["snapshot_id"] == reference["snapshot_id"] for o in offers), "snapshot_mismatch")
        for field in ("selection_id", "offer_id"):
            _require(all(o[field] for o in offers) and len({o[field] for o in offers}) == 2, "duplicate_or_missing_offer_identity")
        for offer in offers:
            _require(offer["market"] == "승패" and offer.get("line") is None and offer.get("label", "") == "", "unsupported_market")
            _require(_number(offer["odds"]) > 1, "invalid_odds")
            _require(0 < _number(offer["market_probability"]) < 1, "invalid_market_probability")
            _require(_time(offer["observed_at"]) <= decision, "offer_after_decision")
        _require(abs(sum(o["market_probability"] for o in offers) - 1) <= .0002, "market_probability_sum")
        base = .9 * sides["home"]["market_probability"] + .1 * vote
        p = _probability(_number(state["feature"]["x"][0]), base, _MODEL["final_fit"])
        side = "home" if p >= .5 else "away"
        picked = sides[side]
        binding = dict(state_hash=state["state_hash"], fixture=target, decision_at=decision.isoformat(),
                       reference=reference, offers=sorted(offers, key=lambda o: o["side"]))
        return _envelope("available", probability=p if side == "home" else 1 - p,
                         home_probability=p, side=side, selection_id=picked["selection_id"],
                         offer_id=picked["offer_id"], odds=picked["odds"], market="승패",
                         probability_basis="frozen_e0_residual_on_fixed91_same_electorate",
                         input_hash=signature(binding), state_hash=state["state_hash"],
                         reference_home_probability=base, fit="final_fit",
                         timing_eligibility="requires_outer_ledger_timing_and_electorate_audit",
                         seed_historical_availability=state["seed_historical_availability"])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)


def infer_archived(feature, *, source_signature, partition):
    """Precomputed feature reproduction, always retrospective/unproven timing.

    partition must name the archived output, not choose a model by performance.
    Validation used selection_fit; evaluation used final_fit. Scores are ignored.
    """
    try:
        _require(partition in ("validation", "evaluation") and feature["role"] == partition, "unsupported_archived_partition")
        _require(source_signature == _MODEL["archive_sources"], "archive_source_signature_mismatch")
        _fixture(feature)
        latest = feature["dynamic"]["e0"]["latest_observation"]
        _require(latest is None or latest < feature["date"], "archived_same_day_result_leak")
        fit_name = "selection_fit" if partition == "validation" else "final_fit"
        p = _probability(_number(feature["dynamic"]["e0"]["x"][0]),
                         _number(feature["reference_home_probability"]), _MODEL[fit_name])
        return _envelope("available", home_probability=p, side="home" if p >= .5 else "away",
                         fit=fit_name, capture_kind="retrospective_archive_reproduction",
                         availability_basis=ARCHIVE_POLICY, contemporary_availability_proven=False)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _unavailable(exc)
