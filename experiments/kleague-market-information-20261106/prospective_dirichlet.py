"""Manually freeze and settle the separate K League Dirichlet comparison."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

import numpy as np
from scipy.special import log_softmax

from forward_cycle import HERE, KST, SEASON, capture, link_new, load_parser, schedule_key, update_manuscript, write_atomic
from score_pilot import collect, settle
from study import encoded, implied, scores
from walk_forward import BASELINE, classwise, dirichlet_regressors, fit

CANDIDATE = "dirichlet_l2_050"
L2 = 0.50
FIRST_ROUND = 133
TRAIN_BEFORE = "2026-11-02"
TRAIN_SHA = "24f7b9de786cfad048bd76bfe6e54534fe32a6602d2d6d6e2a0d2fe5b98360d5"
PILOT = HERE / "prospective-260132.json"
PILOT_SHA = "b325e55720086f544ba5afd927f4c1cccaa513232089ed33e4d921edb105b07e"
MODEL = HERE / "dirichlet-model.json"
MANUSCRIPT = HERE / "MANUSCRIPT.md"
START = "<!-- dirichlet-results-start -->"
END = "<!-- dirichlet-results-end -->"


def now():
    return datetime.now(timezone.utc)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def winner_design(rows):
    logp = np.log(np.array([implied(r["quotes"]["winner"]["odds"]) for r in rows]))
    league = np.array([{1: 1, 2: -1, "K1": 1, "K2": -1}[r["league"]] for r in rows])
    bias = np.column_stack([np.ones(len(rows)), league])
    return logp, classwise(dirichlet_regressors(logp, bias))


def prepare(path):
    raw = (HERE / "historical.picks.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    if digest(raw) != TRAIN_SHA or len(rows) != 893 or any(r["kickoff_kst"][:10] >= TRAIN_BEFORE for r in rows):
        raise ValueError("Training data differs from the fixed historical cohort")
    logp, features = winner_design(rows)
    beta = fit(logp, features, np.array([r["outcome"] for r in rows]), L2)
    model = {"model": CANDIDATE, "l2": L2, "training_sha256": TRAIN_SHA, "train_n": len(rows),
             "train_before_kst": TRAIN_BEFORE, "first_round": 260000 + FIRST_ROUND,
             "issued_at_utc": now().isoformat(), "coefficients": beta.tolist()}
    with path.open("xb") as file:
        file.write(encoded(model))
    return model


def winner_snapshot(path, parser):
    meta = json.loads(path.read_bytes())
    receipt = datetime.fromisoformat(meta["received_at"])
    if (meta["timestamp_basis"] != "http_response_body_completed" or meta["http_status"] != 200
            or receipt.utcoffset() != timezone.utc.utcoffset(None)):
        raise ValueError("Unverified Betman receipt")
    raw = gzip.decompress(path.with_name(meta["body_file"]).read_bytes())
    number = int(meta["request_json"]["gmTs"])
    lottery, rows = parser.decode_snapshot(raw, number, meta["body_sha256"])
    ms = receipt.timestamp() * 1000
    if (lottery["saleProgress"] is not True or lottery["saleStatus"] != "SaleProgress"
            or not lottery["saleStartDate"] <= ms < lottery["saleEndDate"]):
        raise ValueError("Round was not open at capture time")
    candidates = defaultdict(list)
    leagues = {"SC001": "K1", "SC003": "K2"}
    for row in rows:
        if (row.get("leagueCode") not in leagues or row.get("protoStatus") != "2"
                or row.get("unsetSchedule") != "N" or row.get("unsetEndDate") != "N"
                or row.get("gameResult") is not None or row.get("mchScore") is not None
                or not row.get("homeId") or not row.get("awayId")
                or not ms < min(row["gameDate"], row["endDate"])):
            continue
        market = parser.product(row)
        if market and market[0] == "winner":
            key = leagues[row["leagueCode"]], row["gameDate"], row["homeId"], row["awayId"]
            candidates[key].append(market[1] | {"sale_ends_ms": min(row["endDate"], lottery["saleEndDate"])})
    return {"round": number, "received_at_utc": receipt.isoformat(), "response_sha256": meta["body_sha256"],
            "fixtures": [{"league": league, "kickoff_kst": datetime.fromtimestamp(kick / 1000, KST).isoformat(),
                          "home_team_id": home, "away_team_id": away, "quotes": {"winner": offers[0]}}
                         for (league, kick, home, away), offers in sorted(candidates.items()) if len(offers) == 1]}


def forecast(rows, model):
    logp, features = winner_design(rows)
    beta = np.array(model["coefficients"])
    if beta.shape != (12,) or not np.isfinite(beta).all():
        raise ValueError("Invalid frozen coefficients")
    corrected = np.exp(log_softmax(logp + np.einsum("ndk,d->nk", features, beta), axis=1))
    return [r | {"probabilities": {BASELINE: base.tolist(), CANDIDATE: alt.tolist()}}
            for r, base, alt in zip(rows, np.exp(logp), corrected, strict=True)]


def pilot():
    raw = PILOT.read_bytes()
    if digest(raw) != PILOT_SHA:
        raise ValueError("Original 14-fixture freeze changed")
    return json.loads(raw)


def schedules_at(directory):
    cache = {}

    def get(division, year, month):
        key = (division, year, month)
        if key not in cache:
            location = directory / f"{year}-{month:02d}"
            location.mkdir(exist_ok=True)
            cache[key] = collect(location, *key)
        return cache[key]

    return get


def saved(state):
    return [json.loads(path.read_bytes()) for path in sorted(state.glob("round-*/forecast.json"))]


def capture_round(state, number):
    if not FIRST_ROUND <= number <= 9999:
        raise ValueError("Use 133 or a later 2026 round")
    round_id = 260000 + number
    target = state / f"round-{round_id}"
    if target.exists():
        return {"round": round_id, "status": "already_frozen"}
    known = {r["official_fixture_id"] for batch in [pilot(), *saved(state)] for r in batch["fixtures"]}
    raw = MODEL.read_bytes()
    model = json.loads(raw)
    if model["model"] != CANDIDATE or model["first_round"] != 260000 + FIRST_ROUND:
        raise ValueError("Unexpected model card")
    with TemporaryDirectory(dir=state, prefix=".reading-") as temporary:
        scratch = Path(temporary)
        evidence = scratch / "evidence"
        evidence.mkdir()
        parser = load_parser(scratch)
        snapshot = capture(evidence, parser, SEASON, number, parse_snapshot=lambda path: winner_snapshot(path, parser))
        if snapshot is None:
            return {"round": round_id, "status": "no_active_sale", "n_frozen": 0}
        if datetime.fromisoformat(model["issued_at_utc"]) > datetime.fromisoformat(snapshot["received_at_utc"]):
            raise ValueError("Model must be frozen before observing the odds")
        rows = link_new(snapshot, schedules_at(evidence), known)
        predicted = forecast(rows, model) if rows else []
        issued = now()
        fixtures = [r for r in predicted if issued < datetime.fromisoformat(r["kickoff_kst"])
                    and issued.timestamp() * 1000 < r["quotes"]["winner"]["sale_ends_ms"]]
        if not fixtures:
            return {"round": round_id, "status": "no_new_eligible_fixtures", "n_frozen": 0,
                    "n_source": len(snapshot["fixtures"])}
        if len({r["official_fixture_id"] for r in fixtures}) != len(fixtures):
            raise ValueError("Official fixture is linked more than once")
        batch = snapshot | {"fixtures": fixtures, "issued_at_utc": issued.isoformat(), "model_sha256": digest(raw)}
        (evidence / "forecast.json").write_bytes(encoded(batch))
        evidence.rename(target)
    return {"round": round_id, "status": "frozen", "n_frozen": len(fixtures), "issued_at_utc": issued.isoformat()}


def update_new_manuscript(path, batches, settled):
    rows = [r for report in settled if report["metrics"] for r in report["joined"]]
    total = sum(len(batch["fixtures"]) for batch in batches)
    lines = [START, "", f"동결 {total}경기, 비교에 반영한 공식 정산 {len(rows)}경기, "
             f"완료하지 않은 묶음에 속한 경기 {total - len(rows)}개, 수집 {len(batches)}회차."]
    if rows:
        base, candidate = (scores(rows, model) for model in (BASELINE, CANDIDATE))
        loss = [-float(v["log_score"].mean()) for v in (base, candidate)]
        lines += ["", f"로그손실: 승무패 단독 {loss[0]:.5f}, Dirichlet {loss[1]:.5f}, "
                  f"보정 − 단독 {loss[1] - loss[0]:+.5f} nat. "
                  f"최고 확률 픽의 적중: {int(base['hit'].sum())} 대 {int(candidate['hit'].sum())}; "
                  f"동일 배당 단위 ROI: {base['pnl'].mean():+.2%} 대 {candidate['pnl'].mean():+.2%}. "
                  "정산이 끝난 묶음만 포함한 기술 통계다."]
    lines += ["", END]
    text = path.read_text(encoding="utf-8")
    before, marker, rest = text.partition(START)
    _, end, after = rest.partition(END)
    if not marker or not end:
        raise ValueError("Dirichlet result markers are missing")
    updated = before + "\n".join(lines) + after
    if updated != text:
        write_atomic(path, updated.encode("utf-8"))


def settle_all(state, manuscript):
    original = pilot()
    batches = saved(state)
    current_model_hash = digest(MODEL.read_bytes())
    ids = Counter(r["official_fixture_id"] for batch in [original, *batches] for r in batch["fixtures"])
    if any(n != 1 for n in ids.values()) or any(b["model_sha256"] != current_model_hash for b in batches):
        raise ValueError("Duplicate fixture or changed model across frozen cohorts")
    evidence = Path(mkdtemp(dir=state, prefix="settlement-"))
    get = schedules_at(evidence)
    old = settle(original, {league: get(league, 2026, 11) for league in (1, 2)})
    new = []
    for batch in batches:
        keys = {schedule_key(r) for r in batch["fixtures"]}
        result = settle(batch, {key: get(*key) for key in keys}, expected=None, allow_rescheduled=True,
                        challenger=CANDIDATE)
        new.append({"round": batch["round"], **result})
    (evidence / "settlement.json").write_bytes(encoded({"pilot": old, "dirichlet": new}))
    if old["metrics"]:
        update_manuscript(manuscript, 1, 0, old["joined"])
    update_new_manuscript(manuscript, batches, new)
    keys = ("n_frozen", "n_joined", "metrics")
    return {"pilot": {k: old[k] for k in keys}, "dirichlet": [
        {k: entry[k] for k in ("round", *keys)} for entry in new], "evidence": str(evidence)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    take = sub.add_parser("capture")
    take.add_argument("--round", required=True, type=int)
    score = sub.add_parser("settle")
    for action in (take, score):
        action.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(MODEL)
    else:
        args.state.mkdir(parents=True, exist_ok=True)
        result = capture_round(args.state, args.round) if args.command == "capture" else settle_all(args.state, MANUSCRIPT)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
