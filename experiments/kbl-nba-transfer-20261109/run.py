"""Reproduce the KBL-only, chronological NBA transfer experiment."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit, ndtr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURES = ROOT / "experiments/pregame-basketball-20261104/season-records.jsonl"
PRICES = ROOT / "experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz"
ALIASES = ROOT / "data/model_candidates/handicap-v1.json"
FAMILIES = ("efficiency", "components", "rotation", "spatial")
RECORDS = ("fgA", "threepA", "ftA", "fg", "threep", "ft", "offr", "to")
POINTS = np.array([2, 3, 1])


def sha(body):
    return hashlib.sha256(body).hexdigest()


def read_snapshot(folder, game, suffix):
    receipt = json.loads((folder / f"{game}.json").read_text(encoding="utf8"))
    body = gzip.decompress((folder / f"{game}.body.gz").read_bytes())
    url = f"https://api.kbl.or.kr/match/{game}/{suffix}"
    if receipt["game_id"] != game or receipt["url"] != url or receipt["sha256"] != sha(body):
        raise ValueError("receipt")
    return json.loads(body), receipt


def parse_box(body, teams, score):
    players = {}
    for row in body:
        person, stat = row["player"], row["records"]
        player, team = person["pcode"], person["tcode"]
        seconds = stat["playMin"] * 60 + stat["playSec"]
        values = np.array([stat[k] for k in RECORDS])
        if not player or player in players or team not in teams or seconds < 0:
            raise ValueError("player_identity")
        if (
            not np.issubdtype(values.dtype, np.integer)
            or np.any(values < 0)
            or np.any(values[3:6] > values[:3])
            or not 0 <= stat["playSec"] < 60
            or stat["fgtA"] != sum(values[:2])
            or stat["fgt"] != sum(values[3:5])
            or stat["score"] != POINTS @ values[3:6]
        ):
            raise ValueError("player_box_totals")
        players[player] = (team, seconds, values)
    durations = [sum(v[1] for v in players.values() if v[0] == team) for team in teams]
    if durations[0] != durations[1] or durations[0] < 12000 or (durations[0] - 12000) % 1500:
        raise ValueError("team_duration")
    raw = [np.sum([v[2] for v in players.values() if v[0] == team], axis=0) for team in teams]
    if [POINTS @ v[3:6] for v in raw] != score:
        raise ValueError("team_score")
    scale = 12000 / durations[0]
    return players, np.asarray(raw) * scale


def parse_chart(body, players):
    counts = {}
    for row in body["shootLog"]:
        pid = row["pcode"]
        logs = row["logs"]
        if not logs:
            continue
        if pid not in players or pid in counts or row["tcode"] != players[pid][0]:
            raise ValueError("chart_identity")
        inner = made = 0
        for shot in logs:
            x, y, basket = shot["x"], shot["y"], shot["d"]
            if not (0 <= x <= 726 and 0 <= y <= 412) or basket not in ("1", "2") or shot["o"] not in ("O", "X"):
                raise ValueError("chart_coordinate")
            # Left basket in normalized image coordinates, with a fixed 110px interior zone.
            court_x = x if basket == "2" else 726 - x
            inner += (court_x - 55) ** 2 + (y - 206) ** 2 <= 110 ** 2
            made += shot["o"] == "O"
        box = players[pid][2]
        if len(logs) != sum(box[:2]) or made != sum(box[3:5]):
            raise ValueError("chart_box_counts")
        counts[pid] = np.array([inner, len(logs)])
    if any(pid not in counts and sum(v[2][:2]) for pid, v in players.items()):
        raise ValueError("chart_missing_shooter")
    return counts


def load(boxes, charts):
    games, audit, receipts = [], Counter(), []
    records = defaultdict(lambda: defaultdict(list))
    for line in FIXTURES.read_text(encoding="utf8").splitlines():
        r = json.loads(line)
        if r["league"] == "KBL" and r["kind"] in ("fixture", "result"):
            records[r["payload"]["source_fixture_id"]][r["kind"]].append(r)
    for gid, rows in sorted(records.items()):
        if len(rows["fixture"]) != 1 or len(rows["result"]) != 1:
            audit["unpaired_official"] += 1
            continue
        f, r = rows["fixture"][0], rows["result"][0]
        if any(f[k] != r[k] for k in ("kickoff", "home", "away")):
            audit["official_mismatch"] += 1
            continue
        teams = tuple(str(f["payload"][k]) for k in ("home_team_id", "away_team_id"))
        score = [r["payload"][k] for k in ("home_score", "away_score")]
        g = {"id": gid, "day": f["kickoff"][:10], "teams": teams, "score": score}
        games.append(g)
        try:
            body, receipt = read_snapshot(boxes, gid, "player-stat")
            g["players"], g["box"] = parse_box(body, teams, score)
            receipts.append(receipt)
            audit["verified_box"] += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            audit["box_missing" if isinstance(exc, FileNotFoundError) else "box_invalid"] += 1
            continue
        try:
            body, receipt = read_snapshot(charts, gid, "match-chart")
            g["chart"] = parse_chart(body, g["players"])
            receipts.append(receipt)
            audit["verified_chart"] += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            audit["chart_missing" if isinstance(exc, FileNotFoundError) else "chart_invalid:" + str(exc)] += 1
    return games, audit, receipts


def possessions(box):
    return box[0] + box[1] + 0.44 * box[2] - box[6] + box[7]


def profile(team, history):
    games = [(g, g["teams"].index(team)) for g in history]
    own, opp = (np.array([g["box"][i if offset == 0 else 1-i] for g, i in games]) for offset in (0, 1))
    net = np.mean([
        100 * (POINTS @ g["box"][i][3:6] / possessions(g["box"][i])
               - POINTS @ g["box"][1-i][3:6] / possessions(g["box"][1-i]))
        for g, i in games
    ])
    minutes, records, chart = defaultdict(float), defaultdict(lambda: np.zeros(9)), defaultdict(lambda: np.zeros(2))
    for n, (g, _) in enumerate(games):
        scale = 12000 / sum(p[1] for p in g["players"].values() if p[0] == team)
        for pid, (t, seconds, box) in g["players"].items():
            if t != team:
                continue
            equivalent = seconds * scale / 60
            records[pid] += np.r_[box * scale, equivalent]
            if n >= 5:
                minutes[pid] += equivalent / 5
            if pid in g.get("chart", {}):
                chart[pid] += g["chart"][pid]
    mean = own.mean(axis=0)
    projected = np.zeros(8)
    inner, chart_attempts = 0.0, 0.0
    for pid, expected in minutes.items():
        v = records[pid]
        projected += expected * (v[:8] + 40 * mean / 200) / (v[8] + 40)
        attempts = expected * (sum(v[:2]) + 40 * sum(mean[:2]) / 200) / (v[8] + 40)
        inner += attempts * (chart[pid][0] + 5) / (chart[pid][1] + 10)
        chart_attempts += attempts
    return {
        "own": mean, "opp": opp.mean(axis=0), "net": net, "rotation": projected,
        "inner": inner / chart_attempts, "charts": sum("chart" in g for g, _ in games),
        "ids": [g["id"] for g, _ in games],
    }


def score(offense, defense):
    # Mix component rates per possession, then multiply by a common game tempo.
    off_poss, def_poss = possessions(offense), possessions(defense)
    tries = (offense[:3] / off_poss + defense[:3] / def_poss) / 2
    rates = (offense[3:6] + defense[3:6]) / (offense[:3] + defense[:3])
    return float(POINTS @ (tries * rates) * (off_poss + def_poss) / 2)


def features(game, schedule):
    cutoff = (date.fromisoformat(game["day"]) - timedelta(days=4)).isoformat()
    profiles = []
    for team in game["teams"]:
        history = [g for g in schedule[team] if g["day"] <= cutoff][-10:]
        if len(history) < 10 or any("box" not in g for g in history):
            return None
        profiles.append(profile(team, history))
    a, b = profiles
    component = [score(a["own"], b["opp"]), score(b["own"], a["opp"])]
    rotation = [score(a["rotation"], b["opp"]), score(b["rotation"], a["opp"])]
    form = [float(POINTS @ (a["own"][3:6] + b["opp"][3:6]) / 2),
            float(POINTS @ (b["own"][3:6] + a["opp"][3:6]) / 2)]
    margin = component[0] - component[1]
    return {
        "x": [(a["net"] - b["net"]) / 10, margin / 10, ((rotation[0] - rotation[1]) - margin) / 10,
              (a["inner"] - b["inner"]) * 10],
        "chart_ok": min(a["charts"], b["charts"]) >= 8,
        "score_components": component,
        "score_rotation": rotation,
        "score_form": form,
        "history": [a["ids"], b["ids"]],
    }


def match(games):
    schedule, index, quotes = defaultdict(list), defaultdict(list), defaultdict(list)
    for game in sorted(games, key=lambda g: (g["day"], g["id"])):
        index[(game["day"], game["teams"])].append(game)
        for team in game["teams"]:
            schedule[team].append(game)
    aliases = json.loads(ALIASES.read_text(encoding="utf8"))["KBL"]["aliases"]
    audit = Counter()
    with gzip.open(PRICES, "rt", encoding="utf8") as f:
        for line in f:
            q = json.loads(line)
            if q["league"] != "KBL" or q["day"][:4] not in ("2025", "2026"):
                continue
            audit["archive_prices"] += 1
            teams = tuple(aliases.get("".join(q[k].split())) for k in ("home", "away"))
            quotes[(q["day"], teams)].append(q)
    targets = []
    for key, qs in sorted(quotes.items(), key=lambda item: repr(item[0])):
        gs = index[key]
        if len(gs) != 1 or len(qs) != 1:
            audit["missing_or_ambiguous_link"] += 1
            continue
        g, q = gs[0], qs[0]
        y = int(g["score"][0] > g["score"][1])
        odds = q["odds"]
        if g["score"][0] == g["score"][1] or q["y"] != 1 - y:
            audit["official_result_conflict"] += 1
            continue
        if len(odds) != 2 or not np.all(np.isfinite(odds)) or min(odds) <= 1:
            audit["invalid_price"] += 1
            continue
        audit["matched_" + g["day"][:4]] += 1
        x = features(g, schedule)
        if x is None:
            audit["incomplete_history_" + g["day"][:4]] += 1
            continue
        targets.append(dict(x, id=g["id"], day=g["day"], teams=g["teams"], score=g["score"], y=y,
                            odds=odds, market=odds[1] / sum(odds), quote=q["id"]))
    return targets, audit


def fit_offset(rows, columns):
    x = np.asarray([r["x"][:columns] for r in rows])
    y = np.asarray([r["y"] for r in rows])
    offset = logit([r["market"] for r in rows])

    def objective(beta):
        z = offset + x @ beta
        return np.sum(np.logaddexp(0, z) - y * z) + 5 * (beta @ beta), x.T @ (expit(z) - y) + 10 * beta

    result = minimize(objective, np.zeros(columns), jac=True, method="BFGS")
    if not result.success:
        raise ValueError(result.message)
    return result.x


def metrics(rows, names):
    outcomes = np.array([r["y"] for r in rows])
    odds = np.array([r["odds"] for r in rows])
    days = sorted({r["day"] for r in rows})
    day_index = np.array([days.index(r["day"]) for r in rows])
    rng = np.random.default_rng(20261109)
    samples = rng.multinomial(len(days), np.full(len(days), 1 / len(days)), size=2000)
    denominators = samples @ np.bincount(day_index)
    results, values, picks = {}, {}, {}
    for name in names:
        p = np.clip([r["prob"][name] for r in rows], 1e-12, 1 - 1e-12)
        pick = (p >= 0.5).astype(int)
        hit = pick == outcomes
        net = hit * odds[np.arange(len(rows)), 1 - pick] - 1
        loss = -np.log(np.where(outcomes, p, 1 - p))
        values[name] = np.stack([hit, net, loss], axis=-1)
        picks[name] = pick
        equity = np.r_[0.0, np.cumsum(np.bincount(day_index, weights=net))]
        results[name] = {"hits": int(hit.sum()), "accuracy": float(hit.mean()), "roi": float(net.mean()),
                         "logloss": float(loss.mean()), "drawdown": float(max(np.maximum.accumulate(equity) - equity))}
    for name in names[1:]:
        refs = {"market", names[names.index(name) - 1]}
        for ref in sorted(refs):
            delta = values[name] - values[ref]
            grouped = np.zeros((len(days), 3))
            np.add.at(grouped, day_index, delta)
            ci = np.percentile((samples @ grouped) / denominators[:, None], [2.5, 97.5], axis=0)
            results[name]["vs_" + ref] = {
                "changed": int(np.count_nonzero(picks[name] != picks[ref])),
                "accuracy_roi_logloss_delta": delta.mean(axis=0).tolist(),
                "ci95": ci.T.tolist(),
            }
    return results


def evaluate(rows, start, end, cutoff, spatial):
    eligible = [r for r in rows if not spatial or r["chart_ok"]]
    train = [r for r in eligible if r["day"] <= cutoff]
    test = [r for r in eligible if start <= r["day"] <= end]
    families = FAMILIES if spatial else FAMILIES[:-1]
    names = ("market", *families, "score_components")
    betas = {name: fit_offset(train, n) for n, name in enumerate(families, 1)}
    margin_error = [r["score"][0] - r["score"][1] - (r["score_components"][0] - r["score_components"][1]) for r in train]
    correction = float(np.mean(margin_error))
    scale = float(np.std(margin_error))
    output = []
    for r in test:
        probs = {"market": r["market"]}
        for name, beta in betas.items():
            probs[name] = float(expit(logit(r["market"]) + np.dot(r["x"][:len(beta)], beta)))
        m = r["score_components"][0] - r["score_components"][1]
        probs["score_components"] = float(ndtr((m + correction) / scale))
        output.append(dict(r, prob=probs))
    errors = {}
    for name in ("score_form", "score_components", "score_rotation"):
        delta = np.asarray([r[name] for r in test]) - np.asarray([r["score"] for r in test])
        errors[name] = {"total_mae": float(np.abs(delta.sum(axis=1)).mean()),
                        "margin_mae": float(np.abs(delta[:, 0] - delta[:, 1]).mean())}
    return {"train": len(train), "n": len(test), "date_range": [test[0]["day"], test[-1]["day"]],
            "metrics": metrics(output, names), "score_errors": errors,
            "betas": {n: x.tolist() for n, x in betas.items()}}, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boxes", type=Path, required=True)
    parser.add_argument("--charts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    games, source_audit, receipts = load(args.boxes, args.charts)
    rows, match_audit = match(games)
    rows.sort(key=lambda r: (r["day"], r["id"]))
    summary = {"official_games": len(games), "source_audit": source_audit, "matching": match_audit,
               "inputs": {path.name: sha(path.read_bytes()) for path in (FIXTURES, PRICES, ALIASES)}, "runs": {}}
    predictions = []
    for period, start, end, cutoff in (
        ("validation_2025", "2025-10-01", "2025-12-26", "2025-09-26"),
        ("evaluation_2026", "2026-01-01", "2026-12-31", "2025-12-27"),
    ):
        for group in ("box", "spatial"):
            name = period + "_" + group
            if group == "spatial" and (
                not any(start <= r["day"] <= end and r["chart_ok"] for r in rows)
                or not any(r["day"] <= cutoff and r["chart_ok"] for r in rows)
            ):
                continue
            summary["runs"][name], output = evaluate(rows, start, end, cutoff, group == "spatial")
            predictions.extend(dict(r, run=name) for r in output)
    args.out.mkdir(parents=True, exist_ok=True)
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    (args.out / "source-receipts.json").write_bytes(dump(receipts).encode())
    body = b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in predictions)
    (args.out / "predictions.jsonl.gz").write_bytes(gzip.compress(body, mtime=0))
    summary["predictions_sha256_uncompressed"] = sha(body)
    (args.out / "summary.json").write_bytes(dump(summary).encode())
    for name, run in summary["runs"].items():
        print(name, run["train"], run["n"], run["date_range"])
        for model, m in run["metrics"].items():
            print(f"  {model:18} {m['hits']:3} {m['accuracy']:7.2%} {m['roi']:7.2%} {m['logloss']:.5f}")
    print("source", source_audit, "matching", match_audit)


if __name__ == "__main__":
    main()
