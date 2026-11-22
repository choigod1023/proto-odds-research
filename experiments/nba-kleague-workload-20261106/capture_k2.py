"""Capture K League 2's official final substitution and dismissal timeline."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from threading import Event
from time import sleep

import requests

from kleague import K2, materialize, module

URL = "https://www.kleague.com/api/ddf/match/matchInfo.do"
EVENTS = {"전반시작", "후반시작", "후반종료", "경기종료", "교체", "퇴장", "경고누적"}


def capture(games, raw):
    stopped = Event()

    def fetch(game):
        if stopped.is_set():
            return None
        _, year, league, fixture = game["id"].split(":")
        path = raw / f"{year}-{fixture}.json.gz"
        if path.exists():
            body = gzip.decompress(path.read_bytes())
            received = None
        else:
            response = requests.post(URL, data=dict(year=year, leagueId=league, meetSeq=2, gameId=fixture),
                                     timeout=(5, 12))
            if response.status_code in (403, 429):
                stopped.set()
            response.raise_for_status()
            body = response.content
            received = datetime.now(timezone.utc).isoformat()
        match = json.loads(body)["data"]
        score = [match["homeGoal"], match["awayGoal"]]
        if match["gameStatus"] != "FE" or score != game["score"]:
            raise ValueError(f"final score mismatch: {game['id']}")
        rows = match["firstHalf"] + match["secondHalf"]
        identity = (int(year), int(league), int(fixture))
        if any((row["year"], row["leagueId"], row["gameId"]) != identity for row in rows):
            raise ValueError(f"timeline identity mismatch: {game['id']}")
        if not path.exists():
            path.write_bytes(gzip.compress(body, mtime=0))
        fields = ("eventName", "teamId", "halfType", "timeMin", "timeSec", "playerId", "playerId2")
        return dict(id=game["id"], score=score, received_at=received, sha256=hashlib.sha256(body).hexdigest(),
                    events=[{field: row[field] for field in fields} for row in rows if row["eventName"] in EVENTS])

    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = []
        for game in games:
            if stopped.is_set():
                break
            tasks.append(pool.submit(fetch, game))
            sleep(0.18)
        return [value for task in tasks if (value := task.result()) is not None]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = materialize(args.state, K2, "experiments/kleague2-lineup-challenger-20261103")
    games, _ = module(source / "model.py").load_games()
    raw = args.state / "k2-timeline-bodies"
    raw.mkdir(parents=True, exist_ok=True)
    captured = capture(games, raw)
    if len(captured) != len(games):
        raise SystemExit(f"Capture stopped with {len(captured)}/{len(games)} timelines")
    payload = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in captured)
    args.output.write_bytes(gzip.compress(payload.encode(), mtime=0))
    print(f"Captured {len(captured)} official final timelines")
