"""Replay K League workload corrections on the existing paired official archive."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
import argparse
import gzip
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

K1 = "b8f5117d2ec60f37562007dcd050c62d35e25d23"
K2 = "32c690e55df246db5e122a037fd4eda0cd2c6be2"
OFFICIAL = "1f5ecca83860d61d49c4a91b0ec186e55b7d2778"
RAW1 = "experiments/kleague1-official-lineup-20261103/"
REF = date(2026, 7, 1)
CUTOFF = date(2026, 6, 24)


def git(*args, **kwargs):
    return subprocess.check_output(["git", *args], **kwargs)


def materialize(parent, revision, relative):
    contents = git("archive", revision, relative)
    with tarfile.open(fileobj=io.BytesIO(contents)) as archive:
        for entry in archive:
            if entry.isfile():
                path = parent / entry.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(entry).read())
    return parent / relative


def module(path):
    spec = importlib.util.spec_from_file_location("prior_" + path.parent.name + path.stem, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def jsonlines(raw):
    return [json.loads(line) for line in raw.splitlines()]


def official_events(games):
    queries = []
    for g in games:
        _, year, _, number = g["id"].split(":")
        path = ("season-2025/" if year == "2025" else "") + f"raw/info-{year}-{number}.json"
        queries.append(f"{OFFICIAL}:{RAW1}{path}")
    stream = io.BytesIO(git("cat-file", "--batch", input=("\n".join(queries) + "\n").encode()))
    events = {}
    for g in games:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            continue
        raw = stream.read(int(header[-1]))
        if stream.read(1) != b"\n":
            raise ValueError("invalid Git archive framing")
        match = json.loads(raw)["data"]
        if [match["homeGoal"], match["awayGoal"]] != g["score"] or match["gameStatus"] != "FE":
            raise ValueError(f"official event score mismatch: {g['id']}")
        rows = match["firstHalf"] + match["secondHalf"]
        identity = tuple(map(int, g["id"].split(":")[1:]))
        if any((row["year"], row["leagueId"], row["gameId"]) != identity for row in rows):
            raise ValueError(f"official event identity mismatch: {g['id']}")
        events[g["id"]] = rows
    return events


def durations(starters, actions):
    started = {pid: 0 for pid in starters}
    played = Counter()
    for at, departing, entering in sorted(actions, key=lambda row: row[0]):
        if departing not in started:
            if entering is None:  # an ejected person who is off the pitch
                continue
            return None
        played[departing] += at - started.pop(departing)
        if entering is not None:
            if entering in started:
                return None
            started[entering] = at
    for player, entered in started.items():
        played[player] += 90 - entered
    return played


def regulation_minutes(game, events):
    markers = {event["eventName"] for event in events or []}
    if not {"전반시작", "후반시작"} <= markers or not markers & {"후반종료", "경기종료"}:
        return None
    result = {}
    for team, starters in zip(game["teams"], game["xi"]):
        changes = []
        for event in events:
            kind = event["eventName"]
            if event["teamId"] != team or kind not in {"교체", "퇴장", "경고누적"}:
                continue
            half = event["halfType"]
            if half not in (1, 2):
                return None
            minute = (half - 1) * 45 + min(45, event["timeMin"] + event["timeSec"] / 60)
            changes.append((minute, event["playerId"], event["playerId2"] if kind == "교체" else None))
        minutes = durations(starters, changes)
        if minutes is None:
            return None
        result[team] = minutes
    return result


def features(games, minutes, current_xi):
    output = {}
    history = defaultdict(list)
    for g in sorted(games, key=lambda row: (row["date"], row["id"])):
        sides = []
        for team, actual_xi in zip(g["teams"], g["xi"]):
            past = [row for row in history[team] if row[0] < g["date"]]
            if len(past) < 2 or past[-2][0].year != g["date"].year:
                break
            previous, previous_xi, _ = past[-1]
            earlier_xi = past[-2][1]
            recent = [m for day, _, m in past if g["date"] - day <= timedelta(days=7)]
            if any(m is None for m in recent):
                break
            xi = actual_xi if current_xi else previous_xi
            reference_xi = previous_xi if current_xi else earlier_xi
            sides.append([sum(m.get(p, 0) for m in recent for p in xi) / (11 * 90),
                          min(14, (g["date"] - previous).days) / 7,
                          (11 - len(set(xi) & set(reference_xi))) / 11])
        if len(sides) == 2:
            output[g["id"]] = np.subtract(sides[0], sides[1])
        for team, xi in zip(g["teams"], g["xi"]):
            history[team].append((g["date"], xi, (minutes.get(g["id"]) or {}).get(team)))
    return output


def label(game):
    home, away = game["score"]
    return 0 if home > away else 1 if home == away else 2


def probabilities(base, x, weights):
    adjustment = x @ weights
    logits = np.log(base) + np.column_stack([adjustment, np.zeros(len(x)), -adjustment])
    return np.exp(logits - logsumexp(logits, axis=1, keepdims=True))


def fit(base, x, labels):
    sign = np.array([1, 0, -1])

    def loss(weights):
        p = probabilities(base, x, weights)
        gradient = x.T @ (p @ sign - sign[labels]) / len(labels) + 0.04 * weights
        return np.mean(-np.log(p[np.arange(len(labels)), labels])) + 0.02 * (weights @ weights), gradient

    fitted = minimize(loss, np.zeros(3), jac=True, method="L-BFGS-B")
    if not fitted.success:
        raise ValueError(f"workload fit failed: {fitted.message}")
    return fitted.x


def summarize(rows, draws=5000):
    if not rows:
        return {"n": 0, "arms": {}, "changed": 0}
    arms = {}
    for arm in ("prior", "workload"):
        hits = sum(r[arm] == r["result"] for r in rows)
        net = sum((Decimal(str(r["odds"][r[arm]])) - 1 if r[arm] == r["result"] else Decimal(-1))
                  for r in rows)
        arms[arm] = dict(hits=hits, accuracy=hits / len(rows), roi=float(net / len(rows)),
                         logloss=float(np.mean([-np.log(r[arm + "_p"][r["result"]]) for r in rows])))
    weeks = defaultdict(list)
    for r in rows:
        weeks[date.fromisoformat(r["date"]).isocalendar()[:2]].append(r)
    groups = []
    for week in weeks.values():
        delta = sum((r["odds"][r["workload"]] if r["workload"] == r["result"] else 0) -
                    (r["odds"][r["prior"]] if r["prior"] == r["result"] else 0) for r in week)
        groups.append((len(week), delta))
    groups = np.array(groups)
    sampled = groups[np.random.default_rng(20261106).integers(0, len(groups), (draws, len(groups)))].sum(axis=1)
    ci = np.quantile(sampled[:, 1] / sampled[:, 0], [0.025, 0.975]).tolist() if len(groups) >= 2 else None
    return dict(n=len(rows), arms=arms, changed=sum(r["prior"] != r["workload"] for r in rows),
                paired_roi_ci95=ci, weeks=len(weeks))


def league(state, division):
    prior = K1 if division == 1 else K2
    root = materialize(state, prior, f"experiments/kleague{division}-lineup-challenger-20261103")
    model = module(root / "model.py")
    if division == 1:
        games, _ = module(root / "data.py").load_inputs(scoring=True)
        events = official_events(games)
        frozen = jsonlines((root / "frozen/predictions.jsonl").read_bytes())
        snapshots = {r["fixture_id"]: (r["team_p"], r["teams"]) for r in frozen}
        alpha = json.loads((root / "frozen/manifest.json").read_bytes())["selected_alpha"]["team"]
        quote_name = "prices.jsonl.gz"
        eligible = module(root / "data.py").eligible_price
    else:
        games, _ = model.load_games()
        capture = Path(__file__).with_name("k2-official-timelines.jsonl.gz")
        rows = jsonlines(gzip.decompress(capture.read_bytes()))
        source = {r["id"]: r for r in rows}
        if len(source) != len(rows) or set(source) != {g["id"] for g in games}:
            raise ValueError("K League 2 timeline fixture mismatch")
        if any(source[g["id"]]["score"] != g["score"] for g in games):
            raise ValueError("K League 2 timeline score mismatch")
        events = {r["id"]: r["events"] for r in rows}
        frozen = jsonlines((root / "frozen/test-predictions.jsonl").read_bytes())
        snapshots = {r["fixture_id"]: (r["models"]["team_only"]["p"], r["team_ids"]) for r in frozen}
        alpha = json.loads((root / "frozen/model-parameters.json").read_bytes())["team_only"]["alpha"]
        quote_name = "joined-prices.jsonl.gz"
        # The prior validation module imports its sibling named model.
        import sys
        sys.modules["model"] = model
        check = module(root / "evaluate.py").valid_price
        eligible = lambda g, q: q if check(q, g) else None
    minutes = {g["id"]: regulation_minutes(g, events.get(g["id"])) for g in games}
    quotes = [r for r in jsonlines(gzip.decompress((root / "inputs" / quote_name).read_bytes()))
              if r["league_id"] == division]
    prices = {r["fixture_id"]: r for r in quotes}
    if len(prices) != len(quotes):
        raise ValueError("ambiguous price for fixture")
    train = [g for g in games if g["date"].year == 2025]
    dev = [g for g in games if date(2026, 1, 1) <= g["date"] <= CUTOFF]
    # Apply the prior model form and its fixed regularization to earlier-season-only data.
    earlier = model.fit(train, {}, alpha, False, date(2025, 12, 25))
    _, dev_p, _ = model.predict(earlier, dev, {})
    by_id = {g["id"]: g for g in games}
    report = {"official_matches": len(games), "matches_with_minutes": sum(m is not None for m in minutes.values()),
              "frozen_test_matches": len(snapshots), "arms": {}}
    for current_xi in (False, True):
        kind = "lagged_rotation" if not current_xi else "actual_xi_retrospective"
        x = features(games, minutes, current_xi)
        train_rows = [(g, p) for g, p in zip(dev, dev_p) if g["id"] in x]
        weights = fit(np.array([p for _, p in train_rows]), np.array([x[g["id"]] for g, _ in train_rows]),
                      np.array([label(g) for g, _ in train_rows]))
        rows = []
        excluded = Counter()
        for key, (p, teams) in sorted(snapshots.items()):
            g = by_id[key]
            if g["date"] < REF or g["teams"] != teams:
                raise ValueError("frozen fixture identity or split mismatch")
            raw_price = prices.get(key)
            quote = eligible(g, raw_price) if raw_price else None
            if key not in x:
                excluded["missing_prior_features"] += 1
            if quote is None:
                excluded["missing_or_ineligible_quote"] += 1
            if key not in x or quote is None:
                continue
            if (raw_price["official_score"] != g["score"]
                    or str(raw_price["settlement_code"]) != str(label(g))):
                raise ValueError("price and official score disagree")
            odds = [raw_price["prices"][side] for side in ("home", "draw", "away")]
            p2 = probabilities(np.array([p]), np.array([x[key]]), weights)[0]
            rows.append(dict(id=key, date=g["date"].isoformat(), result=label(g), odds=odds,
                             product=[raw_price[k] for k in ("gmId", "gmTs", "matchSeq")],
                             prior=int(np.argmax(p)), workload=int(np.argmax(p2)),
                             prior_p=p, workload_p=p2.tolist()))
        report["arms"][kind] = dict(training_n=len(train_rows), weights=weights.tolist(),
                                    exclusions=dict(excluded), **summarize(rows), rows=rows)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {f"K{division}": league(args.state, division) for division in (1, 2)}
    picks = args.output.with_suffix(".picks.jsonl")
    with picks.open("w", encoding="utf8", newline="\n") as stream:
        for division, summary in report.items():
            for variant, arm in summary["arms"].items():
                for row in arm.pop("rows"):
                    stream.write(json.dumps(dict(league=division, variant=variant, **row), allow_nan=False) + "\n")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")
