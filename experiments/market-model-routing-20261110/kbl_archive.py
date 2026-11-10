"""Read the pinned KBL research ledgers without depending on a PR merge."""

import gzip
import json
import subprocess

REVISION = "68bdbc7ea6ef0a2035e885c66340de7b1454e232"
ROOT = "experiments/kbl-nba-transfer-20261109"
FROZEN = "kbl-team-margin-handicap-v1"


def read(repo, path):
    blob = subprocess.check_output(["git", "show", f"{REVISION}:{ROOT}/{path}"], cwd=repo)
    return [json.loads(line) for line in gzip.decompress(blob).splitlines()]


def rows(repo):
    base = read(repo, "predictions.jsonl.gz")
    expected = {r["id"]: r for r in base if r["run"] == "evaluation_2026_box"}
    spatial = {r["id"]: r for r in base if r["run"] == "evaluation_2026_spatial_player_audit"}
    lineup = {r["id"]: r for r in read(repo, "lineup-predictions.jsonl.gz")}
    if len(expected) != 152 or spatial.keys() != expected.keys() or lineup.keys() != expected.keys():
        raise ValueError("Unexpected KBL evaluation fixture set")
    for gid, row in expected.items():
        for extra in (spatial[gid], lineup[gid]):
            if any(extra[key] != row[key] for key in ("day", "odds", "score", "y")):
                raise ValueError(f"KBL price or outcome mismatch: {gid}")
        p = dict(row["prob"], spatial=spatial[gid]["prob"]["spatial"],
                 player=lineup[gid]["prob"]["player"], pair=lineup[gid]["prob"]["pair"])
        yield {"id": f"kbl:archive:{gid}", "day": row["day"], "sport": "bk", "league": "KBL",
               "market": "win_loss", "period": "full_including_extra", "line": None,
               "odds": row["odds"], "y": 1 - row["y"], "prob": {name: [v, 1 - v] for name, v in p.items()},
               "provenance": f"kbl-689:{REVISION}:win-loss-archive"}
    side = read(repo, "side-markets/predictions.jsonl.gz")
    for row in side:
        market = row["market"]
        reference = FROZEN if market == "handicap" and FROZEN in row["prob"] else "market"
        yield {"id": row["offer_id"], "day": row["day"], "sport": "bk", "league": "KBL",
               "market": market, "period": row["period"], "line": row["line"], "odds": row["odds"],
               "y": 1 - row["y"], "prob": {name: [p, 1 - p] for name, p in row["prob"].items()},
               "reference": reference, "provenance": f"kbl-689:{REVISION}:betman-final"}
