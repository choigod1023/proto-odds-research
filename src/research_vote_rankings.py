"""Fixed, retrospective ranking diagnostics; never imported by serving code."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import comb
import re

RULES = ("frozen_vote", "market_same_product", "market90_vote10", "vote_market_agreement")


def instant(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("finite number required")
    return result


def top(scores):
    winners = [side for side, value in scores.items() if value == max(scores.values())]
    return winners[0] if len(winners) == 1 else None


def _frozen_scores(feature):
    cutoff = instant(feature["kickoff"]) - timedelta(minutes=30)
    if any(instant(feature[key]) >= cutoff for key in ("as_of", "captured_at", "market_observed_at")):
        raise ValueError("frozen clock is not before T30")
    as_of, captured = instant(feature["as_of"]), instant(feature["captured_at"])
    if as_of > captured or instant(feature["market_observed_at"]) > as_of:
        raise ValueError("source clock is after frozen decision")
    evidence = feature["evidence"]
    if not evidence or any(instant(row["observed_at"]) >= cutoff for row in evidence):
        raise ValueError("vote receipt is not before T30")
    if any(instant(row["observed_at"]) > as_of for row in evidence):
        raise ValueError("vote was received after frozen decision")
    if len({row["author"] for row in evidence}) != len(evidence):
        raise ValueError("duplicate author within fixture")
    if len({row["id"] for row in evidence}) != len(evidence):
        raise ValueError("duplicate vote ID")
    for key in ("source_url", "content_hash"):
        present = [row[key] for row in evidence if row.get(key)]
        if len(set(present)) != len(present):
            raise ValueError("duplicate source evidence: " + key)
    votes = feature["vote_counts"]
    if any(type(value) is not int or value < 0 for value in votes.values()):
        raise ValueError("invalid votes")
    if sum(votes.values()) != len(evidence) or Counter(row["side"] for row in evidence) != +Counter(votes):
        raise ValueError("votes do not match frozen author evidence")
    options = feature["options"]
    expected = {"홈", "무", "원정"} if feature["market"] == "승무패" else {"홈", "원정"}
    if feature["market"] not in {"승무패", "승패"} or feature["label"]:
        raise ValueError("only original full-game moneyline is admissible")
    if len(options) != len(expected) or {row["selection"] for row in options} != expected:
        raise ValueError("incomplete or duplicate frozen product roster")
    if any(value > 0 and side not in expected for side, value in votes.items()):
        raise ValueError("vote outside frozen product")
    for row in options:
        if row["market"] != feature["market"] or row["label"] != feature["label"] or row["line"] is not None:
            raise ValueError("different product in frozen roster")
        if number(row["odds"]) <= 1 or not 0 < number(row["market_probability"]) < 1:
            raise ValueError("invalid frozen price or probability")
    market = {row["selection"]: number(row["market_probability"]) for row in options}
    total = sum(market.values())
    if not Decimal("0.99") < total < Decimal("1.01"):
        raise ValueError("frozen probabilities do not form a complete product")
    market = {side: value / total for side, value in market.items()}
    return expected, evidence, votes, market


def _ranks(expected, evidence, votes, market):
    vote = top({side: votes.get(side, 0) for side in expected})
    favorite = top(market)
    mixed = {side: Decimal("0.90") * market[side]
             + Decimal("0.10") * votes.get(side, 0) / len(evidence) for side in expected}
    return dict(frozen_vote=vote, market_same_product=favorite,
                market90_vote10=top(mixed),
                vote_market_agreement=vote if vote == favorite else None)


def select_rules(feature):
    """Actual saved operating picks; no result argument exists."""
    if feature["policy"] != "tipster-vote-v1" or feature["selection_source"] != "actual_tipster_vote":
        raise ValueError("not the actual vote cohort")
    scores = _frozen_scores(feature)
    choices = _ranks(*scores)
    vote = choices["frozen_vote"]
    if vote is None or vote != feature["original_selection"]:
        raise ValueError("not the saved unique vote winner")
    saved = next(row for row in feature["options"] if row["selection"] == vote)
    if (saved["selection_id"] != feature["original_selection_id"]
            or saved["offer_id"] != feature["original_offer_id"]
            or number(saved["odds"]) != number(feature["original_odds"])):
        raise ValueError("saved selected price or identity differs")
    return choices


def select_replayed_rules(feature):
    """Historical counterfactual from saved inputs, not an operating-policy grade."""
    if feature.get("comparison_mode") != "counterfactual_saved_pregame_votes":
        raise ValueError("explicit counterfactual mode required")
    if feature.get("capture_kind") == "quote_observation_not_model_prediction":
        if feature.get("captured_at") is not None:
            raise ValueError("a quote must not fabricate forecast capture")
        observed = instant(feature["quote_observed_at"])
        if observed != instant(feature["as_of"]) or observed != instant(feature["market_observed_at"]):
            raise ValueError("counterfactual input clock differs from observed quote")
        # This is only the recorded quote clock, not a proved HTTP receipt.
        # Legacy snapshots use batch-start time; captured_at stays None in the
        # artifact, and their timing limitation must remain in its audit.
        feature = dict(feature, captured_at=feature["quote_observed_at"])
    return _ranks(*_frozen_scores(feature))


def grade_selected(feature, label, side, stake=10000):
    if side is None:
        return None
    if type(stake) is not int or stake <= 0:
        raise ValueError("positive integer stake required")
    if label["event_id"] != feature["event_id"] or label["snapshot_id"] != feature["snapshot_id"]:
        raise ValueError("result fixture mismatch")
    if instant(label["kickoff"]) != instant(feature["kickoff"]) or label["market"] != feature["market"]:
        raise ValueError("result product mismatch")
    if label["label"] != feature["label"] or label["n_way"] != len(feature["options"]):
        raise ValueError("result line or n-way mismatch")
    if label["result_action"] != "결과보기" or not re.fullmatch(r"\d+:\d+", label["result_text"]):
        raise ValueError("unconfirmed official score")
    home, away = map(int, label["result_text"].split(":"))
    winner = "홈" if home > away else "원정" if home < away else "무"
    if winner == "무" and label["n_way"] == 2:
        raise ValueError("two-way draw needs separate refund evidence")
    option = next(row for row in feature["options"] if row["selection"] == side)
    hit = side == winner
    pnl = (number(option["odds"]) - 1) * stake if hit else -Decimal(stake)
    return dict(snapshot_id=feature["snapshot_id"], event_id=feature["event_id"],
                kickoff=feature["kickoff"], sport=feature["sport"], selection=side,
                odds=option["odds"], hit=hit, pnl_won=str(pnl))


def summarize(rows, eligible, stake):
    hit = sum(row["hit"] for row in rows)
    net = sum((Decimal(row["pnl_won"]) for row in rows), Decimal(0))
    return dict(bets=len(rows), hit=hit, miss=len(rows)-hit, abstained=eligible-len(rows),
                coverage_percent=100*len(rows)/eligible if eligible else None,
                accuracy_percent=100*hit/len(rows) if rows else None,
                roi_percent=float(100*net/(stake*len(rows))) if rows else None,
                net_won=str(net), staked_won=stake*len(rows))


def evaluate(features, labels, stake=10000, *, selector=select_rules):
    if type(stake) is not int or stake <= 0 or not features:
        raise ValueError("nonempty fixed cohort and positive stake required")
    ids = [row["snapshot_id"] for row in features]
    if len(set(ids)) != len(ids) or len({row["event_id"] for row in features}) != len(features):
        raise ValueError("one frozen snapshot per fixture required")
    label_ids = [row["snapshot_id"] for row in labels]
    if len(set(label_ids)) != len(label_ids) or set(ids) != set(label_ids):
        raise ValueError("exact complete label coverage required")
    # Freeze every choice before touching any outcomes.
    choices = {row["snapshot_id"]: selector(row) for row in features}
    label_index = {row["snapshot_id"]: row for row in labels}
    results, all_rows = {}, {}
    for rule in RULES:
        rows = [grade_selected(row, label_index[row["snapshot_id"]], choices[row["snapshot_id"]][rule], stake)
                for row in features]
        rows = [row for row in rows if row is not None]
        all_rows[rule] = {row["snapshot_id"]: row for row in rows}
        by_sport = defaultdict(list)
        for row in rows:
            by_sport[row["sport"]].append(row)
        results[rule] = summarize(rows, len(features), stake)
        results[rule]["by_sport"] = {sport: summarize(values, sum(f["sport"] == sport for f in features), stake)
                                      for sport, values in sorted(by_sport.items())}
        results[rule]["rows"] = rows
    comparisons = {}
    base = all_rows["frozen_vote"]
    for rule in RULES[1:]:
        alternate = all_rows[rule]
        common = sorted(base.keys() & alternate.keys())
        better = sum(alternate[s]["hit"] and not base[s]["hit"] for s in common)
        worse = sum(base[s]["hit"] and not alternate[s]["hit"] for s in common)
        discordant = better + worse
        p = min(1., 2*sum(comb(discordant, i) for i in range(min(better, worse)+1))/2**discordant) if discordant else 1.
        comparisons[rule] = dict(common_fixtures=len(common), improved=better, worsened=worse,
                                 changed_pick_count=sum(base[s]["selection"] != alternate[s]["selection"] for s in common),
                                 exploratory_exact_mcnemar_p=p,
                                 baseline_on_same_subset=summarize([base[s] for s in common], len(features), stake))
    return dict(version="same-period-vote-ranking-v1", eligible_fixtures=len(features),
                choices=choices, results=results, paired=comparisons,
                caveat="Retrospective, tiny previously viewed cohort; no new holdout, calibration or promotion claim.")
