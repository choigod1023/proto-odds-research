"""Link frozen probabilities to official K League schedule results."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests

from study import encoded, scores

URL = "https://www.kleague.com/getScheduleList.do"


def collect(evidence, league, year=2026, month=11):
    request = {"leagueId": league, "year": str(year), "month": f"{month:02d}", "teamId": "", "ticketYn": ""}
    with requests.post(URL, json=request, timeout=(5, 10), allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError(f"Official K League {league} schedule returned HTTP {response.status_code}")
        raw = response.raw.read(3_000_001, decode_content=True)
    if len(raw) > 3_000_000:
        raise ValueError("Official schedule response exceeded size limit")
    receipt = {"url": URL, "request": request, "received_at_utc": datetime.now(timezone.utc).isoformat(),
               "sha256": hashlib.sha256(raw).hexdigest()}
    document = json.loads(raw)
    if str(document["resultCode"]) != "200":
        raise ValueError(f"Official K League {league} result code was not successful")
    (evidence / f"k{league}.json").write_bytes(raw)
    (evidence / f"k{league}.receipt.json").write_bytes(encoded(receipt))
    return receipt, document["data"]["scheduleList"]


def settle(frozen, schedules, *, expected=14, allow_rescheduled=False):
    fixtures = frozen["fixtures"]
    if (not fixtures or (expected is not None and len(fixtures) != expected)
            or len({row["official_fixture_id"] for row in fixtures}) != len(fixtures)):
        raise ValueError("Frozen fixture coverage differs from protocol")
    issued = datetime.fromisoformat(frozen["issued_at_utc"])
    joined, waiting = [], []
    for row in fixtures:
        _, year, division, game = row["official_fixture_id"].split(":")
        kickoff = datetime.fromisoformat(row["kickoff_kst"])
        if int(year) != kickoff.year:
            raise ValueError(f"Official fixture year disagrees: {row['official_fixture_id']}")
        key = (int(division), kickoff.year, kickoff.month) if expected is None else int(division)
        receipt, schedule = schedules[key]
        matches = [match for match in schedule if (match["leagueId"], match["gameId"]) == (int(division), int(game))]
        if len(matches) != 1:
            if allow_rescheduled and not matches:
                waiting.append({"id": row["official_fixture_id"], "status": "missing_from_original_month"})
                continue
            raise ValueError(f"Official fixture identity is ambiguous: {row['official_fixture_id']}")
        match = matches[0]
        official_date = f"{match['gameDate'].replace('.', '-')}T{match['gameTime']}"
        if (not issued < kickoff
                or (match["homeTeam"], match["awayTeam"]) != (row["home_team_id"], row["away_team_id"])):
            raise ValueError(f"Official team or kickoff mismatch: {row['official_fixture_id']}")
        if official_date != kickoff.strftime("%Y-%m-%dT%H:%M"):
            if allow_rescheduled:
                waiting.append({"id": row["official_fixture_id"], "status": "kickoff_changed"})
                continue
            raise ValueError(f"Official team or kickoff mismatch: {row['official_fixture_id']}")
        if (match["gameStatus"], match["endYn"]) != ("FE", "Y"):
            waiting.append({"id": row["official_fixture_id"], "status": match["gameStatus"]})
            continue
        home, away = match["homeGoal"], match["awayGoal"]
        if any(type(goal) is not int or goal < 0 for goal in (home, away)):
            raise ValueError(f"Final score is malformed: {row['official_fixture_id']}")
        if (datetime.fromisoformat(receipt["received_at_utc"]) <= kickoff
                or match.get("homeGoal6") is not None or match.get("awayGoal6") is not None):
            raise ValueError(f"Score cannot be verified as a completed regular fixture: {row['official_fixture_id']}")
        outcome = 0 if home > away else 1 if home == away else 2
        joined.append(row | {"score": [home, away], "outcome": outcome, "official_sha256": receipt["sha256"]})
    metrics = None
    if not waiting:
        values = {model: scores(joined, model) for model in joined[0]["probabilities"]}
        metrics = {model: {"mean_log_score": float(result["log_score"].mean()), "hits": int(result["hit"].sum()),
                           "roi": float(result["pnl"].mean())} for model, result in values.items()}
        metrics["paired_log_gain_nats"] = float(
            (values["poisson_three"]["log_score"] - values["winner_implied"]["log_score"]).mean())
    return {"n_frozen": len(fixtures), "n_joined": len(joined), "unresolved": waiting, "metrics": metrics,
            "joined": joined}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True, help="New directory for this official schedule capture")
    args = parser.parse_args()
    raw = args.frozen.read_bytes()
    frozen = json.loads(raw)
    args.evidence.mkdir(parents=True, exist_ok=False)
    schedules = {league: collect(args.evidence, league) for league in (1, 2)}
    result = settle(frozen, schedules)
    result["frozen_sha256"] = hashlib.sha256(raw).hexdigest()
    (args.evidence / "settlement.json").write_bytes(encoded(result))
    print(json.dumps({key: result[key] for key in ("n_frozen", "n_joined", "unresolved", "metrics")}))
