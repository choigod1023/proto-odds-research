"""Fetch only the historical KBL charts whose official boxes are already cached."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HEADERS = {
    "User-Agent": "CodexResearch/1.0 (bounded retrospective research)",
    "Channel": "WEB",
    "TeamCode": "XX",
    "lang": "ko",
    "X-Requested-With": "XMLHttpRequest",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boxes", type=Path, required=True)
    parser.add_argument("--charts", type=Path, required=True)
    args = parser.parse_args()
    games = sorted(path.name.removesuffix(".body.gz") for path in args.boxes.glob("*.body.gz"))
    if not games:
        raise SystemExit("No official box inputs found")
    args.charts.mkdir(parents=True, exist_ok=True)
    halt = args.charts / "rate-limit.txt"
    if halt.exists():
        raise SystemExit("Collection paused after rate limit: " + halt.read_text())
    count = 0
    for game in games:
        if not re.fullmatch(r"S\d+G\d+N\d+", game):
            raise ValueError(game)
        path = args.charts / (game + ".json")
        if path.is_file():
            continue
        url = f"https://api.kbl.or.kr/match/{game}/match-chart"
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=10) as response:
                body = response.read()
            json.loads(body)
            (args.charts / (game + ".body.gz")).write_bytes(gzip.compress(body, mtime=0))
            receipt = {
                "game_id": game,
                "url": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
            path.write_text(json.dumps(receipt) + "\n", encoding="utf8")
            count += 1
            if count % 50 == 0:
                print(f"new official charts: {count}", flush=True)
        except HTTPError as error:
            if error.code == 429:
                halt.write_text(error.headers.get("Retry-After", "unspecified"))
                raise SystemExit("KBL rate limit; stopping collection") from error
            if error.code != 404:
                raise
            print(f"{game}: HTTP 404", flush=True)
        except (TimeoutError, URLError) as error:
            print(f"{game}: {error}", flush=True)
        time.sleep(1.05)
    print(f"new official charts: {count}", flush=True)


if __name__ == "__main__":
    main()
