"""Compare K League 1X2 picks using co-snapshot G101 prices."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import tarfile

import numpy as np
from scipy.optimize import minimize
from scipy.special import log_softmax

BRIDGE = "4f3ee6b2199b5fda5b87e110d8b1a69ffffb6386"
BASELINES = {
    1: ("b8f5117d2ec60f37562007dcd050c62d35e25d23", "frozen/predictions.jsonl"),
    2: ("32c690e55df246db5e122a037fd4eda0cd2c6be2", "frozen/test-predictions.jsonl"),
}
MODERN = {
    ("1", "1", "축구 승무패", "승무패", 0): "winner",
    ("5", "4", "축구 핸디캡", "일반 정수핸디캡", 2): "handicap",
    ("78", "7", "축구 언더오버", "일반 언더오버", 9): "total",
}
START_TEST = "2026-07-01"
TRAIN_END = "2026-06-24"
SIDES = ("home", "draw", "away")
KST = timezone(timedelta(hours=9))


def git_blob(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"])


def records(raw):
    return [json.loads(line) for line in raw.splitlines()]


def decode_snapshot(raw, round_id, digest):
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Betman response SHA-256 mismatch")
    data = json.loads(raw)
    lottery = data["currentLottery"]
    if lottery["gmId"] != "G101" or int(lottery["gmTs"]) != round_id or int(data["gmTs"]) != round_id:
        raise ValueError("Betman response has a different product round")
    fields = data["compSchedules"]["keys"]
    if len(set(fields)) != len(fields):
        raise ValueError("Duplicate Betman schedule columns")
    return lottery, [dict(zip(fields, values, strict=True)) for values in data["compSchedules"]["datas"]]


def product(row, score=None):
    if row.get("itemCode") != "SC" or row.get("live") != "N" or row.get("gameSubject") not in (None, ""):
        return None
    if (row.get("gameReject"), row.get("buyReject")) != ("0", "0"):
        return None
    identity = tuple(row.get(k) for k in ("betId", "betTypId", "betNm", "betTypNm", "handi"))
    if identity[:4] == (None,) * 4:
        kind = {0: "winner", 2: "handicap", 9: "total"}.get(row["handi"])
    else:
        kind = MODERN.get(identity)
    if kind is None:
        return None
    line, draw_line, away_line = (row.get(k) for k in ("winHandi", "drawHandi", "loseHandi"))
    if kind == "winner" and (line, draw_line, away_line) == (0, 0, 0):
        outcome = result(score[0] - score[1]) if score else None
    elif kind == "handicap" and type(line) in (int, float) and line in (-2, -1, 1, 2) and away_line == -line:
        outcome = result(score[0] - score[1] + line) if score else None
    elif kind == "total" and line in (2.5, 3.5) and away_line == line and row.get("drawAllot") in (0, None):
        outcome = (0 if sum(score) < line else 2) if score else None
    else:
        return None
    columns = ("winAllot", "loseAllot") if kind == "total" else ("winAllot", "drawAllot", "loseAllot")
    odds = [row.get(key) for key in columns]
    if not all(type(value) in (int, float) and math.isfinite(value) and 1 < value < 1000 for value in odds):
        return None
    if score is not None and str(row.get("gameResult")) != str(outcome):
        return None
    return kind, {"id": row["matchSeq"], "line": line, "odds": odds}


def result(margin):
    return 0 if margin > 0 else 2 if margin < 0 else 1


def match_snapshot(rows, bridge):
    candidates = defaultdict(list)
    identity = (bridge["betman_league_code"], bridge["home_team_id"], bridge["away_team_id"],
                int(datetime.fromisoformat(bridge["kickoff_kst"]).timestamp() * 1000))
    score = bridge["official_score"]
    for row in rows:
        if tuple(row.get(k) for k in ("leagueCode", "homeId", "awayId", "gameDate")) != identity:
            continue
        match = product(row, score)
        if match:
            candidates[match[0]].append(match[1])
    expected = {"id": bridge["matchSeq"], "line": 0, "odds": [bridge["prices"][k] for k in SIDES]}
    if candidates["winner"] != [expected]:
        raise ValueError(f"Winner quote differs from official-price bridge: {bridge['fixture_id']}")
    return select_quotes(candidates)


def select_quotes(candidates):
    for kind in ("winner", "total", "handicap"):
        if not candidates[kind]:
            return None, f"missing_{kind}"
        if len({quote["line"] for quote in candidates[kind]}) != len(candidates[kind]):
            return None, f"ambiguous_{kind}_line"
    # Closest to zero, with the negative home line first for a fixed tie-break.
    handicap = min(candidates["handicap"], key=lambda q: (abs(q["line"]), q["line"]))
    total = min(candidates["total"], key=lambda q: q["line"])
    return {"winner": candidates["winner"][0], "total": total, "handicap": handicap}, None


def snapshots():
    prefix = "experiments/kleague-official-prices-20261103/"
    manifest = json.loads(git_blob(BRIDGE, prefix + "manifest.json"))
    joined = git_blob(BRIDGE, prefix + "joined-prices.jsonl.gz")
    if hashlib.sha256(joined).hexdigest() != manifest["joined_gzip_sha256"]:
        raise ValueError("K League price bridge changed")
    bridge = records(gzip.decompress(joined))
    requested = {prefix + row["response_file"] for row in bridge}
    archive = subprocess.check_output(["git", "archive", BRIDGE, *sorted(requested)])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        bodies = {entry.name: tar.extractfile(entry).read() for entry in tar if entry.name in requested}
    indexed = {}
    cohort = {}
    counts = defaultdict(Counter)
    for row in bridge:
        fixture = row["fixture_id"]
        stats = counts[f"K{row['league_id']}"]
        stats["winner_fixtures"] += 1
        path = prefix + row["response_file"]
        key = (path, row["response_sha256"], row["gmTs"])
        if key not in indexed:
            lottery, indexed[key] = decode_snapshot(gzip.decompress(bodies[path]), row["gmTs"],
                                                    row["response_sha256"])
            if lottery.get("saleProgress") is not False or lottery.get("saleStatus") != "PayoStart":
                raise ValueError("Historical bridge expects a closed settlement response")
        quotes, reason = match_snapshot(indexed[key], row)
        if reason:
            stats[reason] += 1
            continue
        stats["co_snapshot_fixtures"] += 1
        if fixture in cohort:
            raise ValueError("Duplicate bridge fixture")
        cohort[fixture] = row | {"quotes": quotes}
    return cohort, counts


def pregame_snapshot(path):
    meta = json.loads(path.read_bytes())
    if meta.get("timestamp_basis") != "http_response_body_completed" or meta.get("http_status") != 200:
        raise ValueError("Pregame receipt time is not verified")
    receipt = datetime.fromisoformat(meta["received_at"])
    round_id = int(meta["request_json"]["gmTs"])
    body = gzip.decompress(path.with_name(meta["body_file"]).read_bytes())
    lottery, rows = decode_snapshot(body, round_id, meta["body_sha256"])
    millis = receipt.timestamp() * 1000
    if (lottery["saleProgress"] is not True or lottery["saleStatus"] != "SaleProgress"
            or not lottery["saleStartDate"] <= millis < lottery["saleEndDate"]):
        raise ValueError("Round was not open at capture time")
    groups = defaultdict(lambda: defaultdict(list))
    leagues = {"SC001": "K1", "SC003": "K2"}
    for row in rows:
        if (row.get("leagueCode") not in leagues or row.get("protoStatus") != "2"
                or row.get("unsetSchedule") != "N" or row.get("unsetEndDate") != "N"
                or row.get("gameResult") is not None or row.get("mchScore") is not None
                or not row.get("homeId") or not row.get("awayId")
                or not millis < min(row["gameDate"], row["endDate"])):
            continue
        market = product(row)
        if market:
            key = leagues[row["leagueCode"]], row["gameDate"], row["homeId"], row["awayId"]
            groups[key][market[0]].append(market[1])
    fixtures = []
    for (league, kickoff, home, away), candidates in sorted(groups.items()):
        selected, _ = select_quotes(candidates)
        if selected:
            fixtures.append({"league": league, "kickoff_kst": datetime.fromtimestamp(kickoff / 1000, KST).isoformat(),
                             "home_team_id": home, "away_team_id": away, "quotes": selected})
    return {"round": round_id, "received_at_kst": receipt.astimezone(KST).isoformat(),
            "response_sha256": meta["body_sha256"], "coverage": dict(Counter(row["league"] for row in fixtures)),
            "fixtures": fixtures}


def link_pregame_schedule(snapshot):
    schedules = defaultdict(list)
    for division, name in ((1, "schedule2026.json"), (2, "census-2026.json")):
        revision, _ = BASELINES[division]
        source = json.loads(git_blob(revision, f"experiments/kleague{division}-lineup-challenger-20261103/inputs/{name}"))
        if division == 1:
            rows = [(r["day"], r["kickoff_local_display"], r["home_team_id"], r["away_team_id"], r["fixture_id"])
                    for r in source["targets"]]
        else:
            rows = [(r["gameDate"].replace(".", "-"), r["gameTime"], r["homeTeam"], r["awayTeam"],
                     f"kleague:2026:2:{r['gameId']}") for r in source["fixtures"]]
        for day, time, home, away, key in rows:
            schedules[(f"K{division}", f"{day}T{time}:00+09:00", home, away)].append(key)
    linked = Counter()
    for row in snapshot["fixtures"]:
        key = tuple(row[k] for k in ("league", "kickoff_kst", "home_team_id", "away_team_id"))
        ids = schedules[key]
        row["official_fixture_id"] = ids[0] if len(ids) == 1 else None
        if len(ids) == 1:
            linked[row["league"]] += 1
    snapshot["official_schedule_matches"] = dict(linked)
    return snapshot


def implied(quote):
    inverse = 1 / np.array(quote["odds"])
    return inverse / inverse.sum()


def features(quotes, extra):
    winner = np.log(implied(quotes["winner"]))
    base = [winner[0] - winner[1], winner[2] - winner[1]]
    if not extra:
        return base
    total = np.log(implied(quotes["total"]))
    handicap = np.log(implied(quotes["handicap"]))
    return base + [quotes["total"]["line"] - 2.5, total[1] - total[0], quotes["handicap"]["line"],
                   handicap[0] - handicap[1], handicap[2] - handicap[1]]


def predict(train, evaluation, extra):
    def arrays(rows):
        return (np.array([features(row["quotes"], extra) for row in rows]),
                np.log([implied(row["quotes"]["winner"]) for row in rows]))

    x, market = arrays(train)
    mean = x.mean(axis=0)
    scale = np.maximum(x.std(axis=0), 0.15)
    x = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    observed = np.eye(3)[[row["result"] for row in train]]

    def loss(flat):
        weights = flat.reshape(-1, 3)
        logp = log_softmax(market + x @ weights, axis=1)
        gradient = x.T @ (np.exp(logp) - observed) / len(x) + 0.1 * weights
        return -(observed * logp).sum() / len(x) + 0.05 * (flat @ flat), gradient.ravel()

    fit = minimize(loss, np.zeros(x.shape[1] * 3), jac=True, method="L-BFGS-B")
    if not fit.success:
        raise ValueError(f"Market calibration failed: {fit.message}")
    test, base = arrays(evaluation)
    test = np.column_stack((np.ones(len(test)), (test - mean) / scale))
    return np.exp(log_softmax(base + test @ fit.x.reshape(-1, 3), axis=1))


def evaluate(data, division):
    revision, name = BASELINES[division]
    frozen = records(git_blob(revision, f"experiments/kleague{division}-lineup-challenger-20261103/{name}"))
    train = []
    rows = []
    for key, r in sorted(data.items(), key=lambda item: (item[1]["kickoff_kst"], item[0])):
        if r["league_id"] != division or r["kickoff_kst"][:10] > TRAIN_END:
            continue
        train.append(r | {"result": result(r["official_score"][0] - r["official_score"][1])})
    for prior in frozen:
        key = prior["fixture_id"]
        if key not in data:
            continue
        r = data[key]
        teams = prior["teams"] if division == 1 else prior["team_ids"]
        baseline = prior["team_p"] if division == 1 else prior["models"]["team_only"]["p"]
        frozen_date = prior["day"] if division == 1 else prior["kickoff_kst"]
        compared_date = r["kickoff_kst"][:10] if division == 1 else r["kickoff_kst"]
        if (teams != [r["home_team_id"], r["away_team_id"]] or frozen_date != compared_date
                or r["kickoff_kst"][:10] < START_TEST):
            raise ValueError("Frozen fixture is outside the held-out cohort")
        if division == 1 and (not prior["price"] or prior["price"]["product_id"] != ["G101", r["gmTs"], r["matchSeq"]]
                              or prior["price"]["odds"] != r["quotes"]["winner"]["odds"]):
            raise ValueError("K League 1 frozen quote differs from the comparison quote")
        rows.append(r | {"baseline": baseline, "result": result(r["official_score"][0] - r["official_score"][1])})
    rows.sort(key=lambda r: (r["kickoff_kst"], r["fixture_id"]))
    if not train or not rows:
        raise ValueError("No common training or evaluation fixtures")
    probabilities = {
        "team_poisson": np.array([r["baseline"] for r in rows]),
        "winner_implied": np.array([implied(r["quotes"]["winner"]) for r in rows]),
        "winner_calibrated": predict(train, rows, False),
        "cross_market": predict(train, rows, True),
    }
    outcomes = np.array([r["result"] for r in rows])
    odds = np.array([r["quotes"]["winner"]["odds"] for r in rows])
    arm = {}
    money = {}
    picks = {}
    for key, p in probabilities.items():
        picks[key] = chosen = p.argmax(axis=1)
        hits = chosen == outcomes
        money[key] = np.where(hits, odds[np.arange(len(rows)), chosen] - 1, -1)
        arm[key] = dict(hits=int(hits.sum()), accuracy=float(hits.mean()), roi=float(money[key].mean()),
                        log_loss=float(-np.log(p[np.arange(len(rows)), outcomes]).mean()))
    weeks = defaultdict(list)
    for idx, row in enumerate(rows):
        weeks[date.fromisoformat(row["kickoff_kst"][:10]).isocalendar()[:2]].append(idx)
    paired = {}
    draws = np.random.default_rng(20261106).integers(0, len(weeks), (5000, len(weeks)))
    for key in ("winner_implied", "winner_calibrated", "team_poisson"):
        difference = money["cross_market"] - money[key]
        blocks = np.array([(len(indices), difference[indices].sum()) for indices in weeks.values()])
        sample = blocks[draws].sum(axis=1)
        paired[key] = {"pick_changes": int((picks["cross_market"] != picks[key]).sum()),
                       "roi_delta_ci95": np.quantile(sample[:, 1] / sample[:, 0], [0.025, 0.975]).tolist()}
    ledger = [
        {k: r[k] for k in ("fixture_id", "kickoff_kst", "response_sha256", "received_at", "gmTs",
                           "official_score", "result", "quotes")}
        | {"picks": {k: int(v[i]) for k, v in picks.items()},
           "probabilities": {k: p[i].tolist() for k, p in probabilities.items()}}
        for i, r in enumerate(rows)
    ]
    return dict(training_n=len(train), frozen_n=len(frozen), test_n=len(rows), weeks=len(weeks),
                first_test=rows[0]["kickoff_kst"], last_test=rows[-1]["kickoff_kst"], arms=arm,
                cross_market_vs=paired), ledger


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pregame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cohort, counts = snapshots()
    summary = {"pregame_snapshot": link_pregame_schedule(pregame_snapshot(args.pregame)),
               "historical_pregame_verified_fixtures": 0, "timing": "closed_archive_retrospective",
               "coverage": counts, "leagues": {}}
    with args.output.with_suffix(".picks.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for league in (1, 2):
            summary["leagues"][f"K{league}"], picks = evaluate(cohort, league)
            for row in picks:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
    args.output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
