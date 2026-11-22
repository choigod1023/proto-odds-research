"""Independently check the frozen compound-Poisson model by a PGF recurrence.

The fitted model uses convolution of thinned Poisson counts. This checker does
not import its code: p[k]=sum(j*lambda[j]*p[k-j])/k reconstructs the score law.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

TEAMS = set("ATL BOS BKN CHA CHI CLE DAL DEN DET GS HOU IND LAC LAL MEM MIA MIL MIN NO NY OKC ORL PHI PHX POR SAC SA TOR UTAH WSH".split())


def pmf(jumps, maximum=600):
    if set(jumps) != {1, 2, 3} or any(not math.isfinite(x) or x < 0 for x in jumps.values()):
        raise ValueError("invalid_jump_rates")
    p = [math.exp(-sum(jumps.values()))]
    for k in range(1, maximum+1):
        p.append(sum(j*lam*p[k-j] for j, lam in jumps.items() if j <= k)/k)
    if abs(sum(p)-1) > 1e-12:
        raise ValueError("score_tail_or_numeric_error")
    return p


def win_probability(home, away):
    cumulative, result = 0., 0.
    for hp, ap in zip(home, away):
        result += hp*(cumulative+.5*ap)
        cumulative += ap
    return result


def load_boxes(cache):
    boxes, receipts, unavailable = {}, 0, 0
    for rec in sorted((cache/"summary").glob("*.receipt.json")):
        receipt = json.loads(rec.read_text(encoding="utf8"))
        absent = rec.name.endswith(".unavailable.receipt.json")
        body = rec.with_name(rec.name.replace(".receipt.json", ".body" if absent else ".json"))
        raw = body.read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            raise ValueError("source_hash_mismatch")
        if absent:
            if receipt["status"] != 404:
                raise ValueError("wrong_unavailable_status")
            unavailable += 1
            continue
        if receipt["status"] != 200:
            raise ValueError("unexpected_source_status")
        receipts += 1
        data = json.loads(raw)
        header = data["header"]
        competition = header["competitions"][0]
        competitors = {c["homeAway"]: c for c in competition["competitors"]}
        teams = [competitors[s]["team"].get("abbreviation") for s in ("home", "away")]
        if not set(teams) <= TEAMS or len(set(teams)) != 2:
            continue
        identifier = rec.name.removesuffix(".receipt.json")
        if str(header["id"]) != identifier or str(competition["id"]) != identifier or identifier in boxes:
            raise ValueError("source_identity_conflict")
        day = datetime.fromisoformat(competition["date"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=9))).date().isoformat()
        table = {b["team"]["abbreviation"]: b for b in data["boxscore"]["teams"]}
        stats = []
        for side, team in zip(("home", "away"), teams):
            values = {s["name"]: s["displayValue"] for s in table[team]["statistics"]}
            fgm, fga = map(int, values["fieldGoalsMade-fieldGoalsAttempted"].split("-"))
            m3, a3 = map(int, values["threePointFieldGoalsMade-threePointFieldGoalsAttempted"].split("-"))
            mf, af = map(int, values["freeThrowsMade-freeThrowsAttempted"].split("-"))
            orb, tov = int(values["offensiveRebounds"]), int(values["totalTurnovers"])
            if fgm*2+m3+mf != int(competitors[side]["score"]):
                raise ValueError("source_score_identity_failed")
            stats.append([fga-orb+tov+.44*af, fga-a3, fgm-m3, a3, m3, af, mf])
        boxes[identifier] = dict(day=day, teams=teams, stats=stats)
    return boxes, receipts, unavailable


def add(arrays):
    return [sum(values) for values in zip(*arrays)]


def league_prior(boxes, cutoff):
    total = add([side for box in boxes.values() if "2025-01-01" <= box["day"] <= cutoff for side in box["stats"]])
    return [total[a]/total[b] for a, b in ((1, 0), (2, 1), (3, 0), (4, 3), (5, 0), (6, 5))]


def rates(total, prior):
    denominators = [total[i] for i in (0, 1, 0, 3, 0, 5)]
    return [(numerator+prior[i]*strength)/(denominators[i]+strength)
            for i, (numerator, strength) in enumerate(zip(total[1:], (500, 200, 500, 200, 500, 200)))]


def audit(cache, predictions, manifest):
    boxes, receipts, absent = load_boxes(cache)
    cutoffs = {"validation": "2025-08-28", "evaluation": "2025-12-28"}
    priors = {period: league_prior(boxes, cutoff) for period, cutoff in cutoffs.items()}
    for period, prior in priors.items():
        if any(abs(a-b) > 1e-12 for a, b in zip(prior, manifest["priors"][period])):
            raise ValueError("training_prior_mismatch")
    errors, references = [], 0
    for row in predictions:
        phase = {"A": "validation", "B": "evaluation", "validation": "validation", "evaluation": "evaluation"}[row["period"]]
        prior = priors[phase]
        offensive, defensive, opportunities = [], [], []
        for team, ids in zip(row["teams"], row["history_event_ids"]):
            if len(ids) != 10 or len(set(ids)) != 10:
                raise ValueError("not_ten_unique_games")
            own, opponent = [], []
            for identifier in ids:
                src = boxes[identifier]
                if (datetime.fromisoformat(row["day"])-datetime.fromisoformat(src["day"])).days < 4 or identifier == row["espn_id"]:
                    raise ValueError("future_or_target_feature")
                index = src["teams"].index(team)
                own.append(src["stats"][index])
                opponent.append(src["stats"][1-index])
                references += 1
            o, d = add(own), add(opponent)
            offensive.append(rates(o, prior))
            defensive.append(rates(d, prior))
            opportunities.append((o[0]+d[0])/20)
        exposure = math.sqrt(opportunities[0]*opportunities[1])
        distributions = []
        for side in (0, 1):
            values = [min(upper, max(lower, offensive[side][i]*defensive[1-side][i]/prior[i]))
                      for i, (lower, upper) in enumerate(zip((.01, .2, .01, .2, .01, .4), (1.5, .8, 1.5, .8, 1.5, .95)))]
            distributions.append(pmf({2: exposure*values[0]*values[1], 3: exposure*values[2]*values[3], 1: exposure*values[4]*values[5]}))
        probability = win_probability(*distributions)
        errors.append(abs(probability-row["probabilities"]["components"]))
    if not errors or max(errors) > 1e-11:
        raise ValueError("generative_probability_mismatch")
    return dict(n=len(predictions), history_references=references, maximum_probability_error=max(errors),
                method="independent probability-generating-function recurrence; no model imports or fitting",
                verified_summary_200=receipts, verified_summary_404=absent, nba_component_boxes=len(boxes),
                training_priors_recomputed=True, cutoffs=cutoffs,
                limits=["Validates the frozen approximate count model, not its physical correctness or predictive superiority",
                        "Logical four-day lag is not proof of historical receipt times"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("cache", "predictions", "manifest", "output"):
        parser.add_argument("--"+key, type=Path, required=True)
    args = parser.parse_args()
    raw = args.predictions.read_bytes()
    rows = [json.loads(s) for s in gzip.decompress(raw).decode("utf8").splitlines()]
    result = audit(args.cache, rows, json.loads(args.manifest.read_text(encoding="utf8")))
    result["predictions_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    print(json.dumps(result))
