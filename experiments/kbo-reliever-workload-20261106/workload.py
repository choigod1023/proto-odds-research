"""Normalize official KBO pitching tables and compute prior-day workload."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import sys

from collect import WINDOWS

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "research/kbo_forward"), str(ROOT / "src")]
from research.kbo_forward.official import BOX, SCHEDULE, OfficialClient, SourceError, innings_outs

TEAM = {"한화": "HH", "KT": "KT", "삼성": "SS", "두산": "OB", "LG": "LG",
        "키움": "WO", "KIA": "HT", "롯데": "LT", "NC": "NC", "SSG": "SK"}
# KBO's official postponement notices 11439 and 11443.
POSTPONED_2025 = {
    "20250330LGNC0", "20250401WOOB0", "20250401SSHT0", "20250401LGKT0",
    "20250401SKNC0", "20250401LTHH0", "20250402SKNC0", "20250403SKNC0",
}


def aliases(path):
    lookup = defaultdict(set)
    for line in gzip.decompress(path.read_bytes()).splitlines():
        row = json.loads(line)
        year = int(row["date"][:4])
        for side, pitchers in zip(("home", "away"), row["pitchers"]):
            if row[side] not in TEAM:
                continue
            for pitcher in pitchers:
                pid = pitcher["player"].removeprefix("kbo:")
                if pid.isdecimal():
                    lookup[year, TEAM[row[side]], pitcher["name"].strip()].add(pid)
                    if row["curated_score_anchor"]:
                        key = (row["official_id"], TEAM[row[side]], pitcher["name"].strip(),
                               pitcher["outs"], pitcher["runs"], pitcher["k"])
                        lookup[key].add(pid)
    # KBO's player page confirms the new name for player 76430.
    lookup[2025, "LT", "김태혁"].add("76430")
    return lookup


def pitcher_rows(table, year, team, fixture, lookup):
    headers = [c["Text"].strip() for c in table["headers"][0]["row"]]
    names = ("선수명", "등판", "투구수")
    if any(headers.count(name) != 1 for name in names):
        raise ValueError("invalid pitching table columns")
    rows = []
    for row in table["rows"]:
        cells = row["row"]
        name, role, count = (cells[headers.index(k)]["Text"].strip() for k in names)
        if not name or not role or not count.isdecimal():
            raise ValueError("missing official pitching row")
        if name.isdecimal():
            pid = name
        else:
            ids = lookup.get((year, team, name), set())
            if len(ids) > 1:
                outs = innings_outs(cells[headers.index("이닝")]["Text"].strip())
                runs = int(cells[headers.index("실점")]["Text"])
                strikeouts = int(cells[headers.index("삼진")]["Text"])
                ids = ids & lookup.get((fixture, team, name, outs, runs, strikeouts), set())
            if len(ids) != 1:
                raise ValueError("unresolved team-season pitcher name")
            pid = next(iter(ids))
        rows.append({"id": pid, "relief": role != "선발", "pitches": int(count)})
    if len({r["id"] for r in rows}) != len(rows) or sum(not r["relief"] for r in rows) != 1:
        raise ValueError("invalid pitcher identity or starter count")
    return rows


def normalize_year(root, year, lookup):
    client = OfficialClient(root)
    index = {rec["params"]["date"]: rec for rec in client.records
             if rec["url"] == SCHEDULE and rec["status"] == 200}
    excluded = Counter()
    days = {}
    for batch in json.loads((root / "manifest.json").read_bytes()):
        day = batch["date"]
        schedule = index[day.replace("-", "")]
        raw = json.loads(client.verified_body(schedule, SCHEDULE))
        expected = {}
        for game in raw["game"]:
            if str(game["SR_ID"]) != "0":
                continue
            if str(game["CANCEL_SC_ID"]) != "0" and "취소" in str(game.get("CANCEL_SC_NM") or ""):
                continue
            expected[game["G_ID"]] = game
        normalized = {}
        for item in batch["games"]:
            game = item["game"]["source"]
            gid = game["G_ID"]
            if expected.get(gid) != game or gid in normalized or game["G_DT"] != day.replace("-", ""):
                raise ValueError("official schedule mismatch")
            home, away = game["HOME_ID"], game["AWAY_ID"]
            out = {"id": gid + str(year), "home": home, "away": away}
            normalized[gid] = out
            receipt = item["box_receipt"]
            if (gid in POSTPONED_2025 and str(game["GAME_STATE_SC"]) == "4"
                    and str(game["CANCEL_SC_ID"]) != "0"):
                out["postponed"] = True
                continue
            if (str(game["GAME_STATE_SC"]) == "3" and str(game["GAME_RESULT_CK"]) == "1"
                    and str(game["CANCEL_SC_ID"]) == "0"):
                out.update(home_score=int(game["B_SCORE_CN"]), away_score=int(game["T_SCORE_CN"]))
            if receipt is None:
                excluded["missing_or_unfinished_box"] += 1
                continue
            try:
                params = receipt["params"]
                if any(str(params[k]) != str(game[v]) for k, v in
                       (("gameId", "G_ID"), ("seasonId", "SEASON_ID"), ("leId", "LE_ID"), ("srId", "SR_ID"))):
                    raise ValueError("box receipt fixture mismatch")
                box = json.loads(client.verified_body(receipt, BOX))
                if str(box["code"]) != "100":
                    raise ValueError("box error")
                out.update(appearances={
                    home: pitcher_rows(box["tables"][4], year, home, out["id"], lookup),
                    away: pitcher_rows(box["tables"][3], year, away, out["id"], lookup),
                }, box_hash=receipt["sha256"], box_received_at=receipt["finished_at"])
            except (KeyError, TypeError, IndexError, ValueError, SourceError) as exc:
                excluded[str(exc)] += 1
        if normalized.keys() != expected.keys():
            raise ValueError("incomplete official schedule")
        days[day] = {"games": list(normalized.values()), "schedule_hash": schedule["sha256"]}
    return days, dict(excluded)


def workload(days, target, team):
    history = defaultdict(list)
    for gap in range(10, 0, -1):
        day = (target - timedelta(days=gap)).isoformat()
        if day not in days:
            return None
        for game in days[day]["games"]:
            if game.get("postponed") or team not in (game["home"], game["away"]):
                continue
            if "appearances" not in game:
                return None
            for row in game["appearances"][team]:
                history[row["id"]].append((gap, row["relief"], row["pitches"]))
    rank = []
    for pid, appearances in history.items():
        older = [pitches for gap, relief, pitches in appearances if gap >= 4 and relief]
        if older:
            rank.append((-len(older), -sum(older), pid))
    if len(rank) < 3:
        return None
    core = []
    for _, _, pid in sorted(rank)[:3]:
        appearances = history[pid]
        observed_days = {gap for gap, _, _ in appearances}
        core.append({
            "id": pid,
            "pitches_3d": sum(pitches for gap, _, pitches in appearances if gap <= 3),
            "back_to_back": {1, 2} <= observed_days,
            "days_since": min(observed_days),
        })
    return core


def build(state, alias_path, output):
    lookup = aliases(alias_path)
    result = {"alias_sha256": hashlib.sha256(alias_path.read_bytes()).hexdigest(), "years": {}}
    for year in WINDOWS:
        days, excluded = normalize_year(state / year, int(year), lookup)
        start, end = map(date.fromisoformat, WINDOWS[year])
        expected = {(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)}
        if set(days) != expected:
            raise ValueError(f"collection is incomplete for {year}")
        result["years"][year] = {"days": days, "unusable_boxes": excluded}
    output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, sort_keys=True).encode(), mtime=0))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.state, args.aliases, args.output)
