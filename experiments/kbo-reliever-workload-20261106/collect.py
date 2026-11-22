"""Archive bounded contiguous samples from the official KBO schedule and box scores."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "research/kbo_forward"), str(ROOT / "src")]
from research.kbo_forward.official import BOX, OfficialClient, SourceError

WINDOWS = {"2025": ("2025-03-22", "2025-04-15"), "2026": ("2026-03-28", "2026-04-21")}


def collect(state, year):
    start, end = map(date.fromisoformat, WINDOWS[year])
    client = OfficialClient(state / year)
    manifest = []
    for offset in range((end - start).days + 1):
        if offset == 12:
            client = OfficialClient(state / year)
        day = start + timedelta(days=offset)
        schedule = client.schedule([day])
        games = []
        for item in schedule["games"]:
            game = item["source"]
            if str(game["SR_ID"]) != "0":
                continue
            if str(game["CANCEL_SC_ID"]) != "0" and "취소" in str(game.get("CANCEL_SC_NM") or ""):
                continue
            if str(game["GAME_STATE_SC"]) != "3" or str(game["GAME_RESULT_CK"]) != "1":
                games.append({"game": item, "box_receipt": None})
                continue
            params = {"leId": game["LE_ID"], "srId": game["SR_ID"],
                      "seasonId": game["SEASON_ID"], "gameId": game["G_ID"]}
            try:
                _, receipt = client.fetch(BOX, params, refresh_seconds=86400 * 365)
            except SourceError as exc:
                if str(exc) in ("http_403", "http_429", "request_cap", "source_backoff_active"):
                    raise
                print(game["G_ID"], str(exc), flush=True)
                receipt = None
            games.append({"game": item, "box_receipt": receipt})
        manifest.append({"date": day.isoformat(), "games": games})
        print(year, day, len(games), client.count, flush=True)
        output = state / year / "manifest.json"
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf8")
        temporary.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--year", choices=WINDOWS, required=True)
    args = parser.parse_args()
    collect(args.state, args.year)
