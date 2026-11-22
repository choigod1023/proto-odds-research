"""Verify saved postgame KBO schedule cards against official box scores."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
schedules = json.loads((HERE / "schedule-requests.json").read_text(encoding="utf-8"))
boxscores = {x["game_id"]: x for x in json.loads((HERE / "boxscore-requests.json").read_text(encoding="utf-8"))}
assert set(schedules) == set(boxscores)
for game_id, schedule_request in sorted(schedules.items()):
    box_request = boxscores[game_id]
    decoded = []
    for suffix, metadata in (("schedule", schedule_request), ("boxscore", box_request)):
        raw = (HERE / f"{game_id}.{suffix}.raw").read_bytes()
        assert len(raw) == metadata["bytes"]
        assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
        assert metadata.get("status", metadata.get("http_status")) == 200
        decoded.append(json.loads(raw))
    schedule, box = decoded
    matches = [game for game in schedule["game"] if game["G_ID"] == game_id]
    assert len(matches) == 1
    game = matches[0]
    assert game["GAME_STATE_SC"] == "3" and game["GAME_RESULT_CK"] == 1
    assert game["START_PIT_CK"] == 1
    results = {}
    for side, index, prefix in (("away", 0, "T"), ("home", 1, "B")):
        table = json.loads(box["arrPitcher"][index]["table"])
        headers = [cell["Text"] for cell in table["headers"][0]["row"]]
        name_index, appearance_index, result_index = (headers.index(x) for x in ("선수명", "등판", "결과"))
        rows = [row["row"] for row in table["rows"]]
        starters = [row for row in rows if row[appearance_index]["Text"] == "선발"]
        assert len(starters) == 1 and starters[0] == rows[0]
        name = starters[0][name_index]["Text"].strip()
        assert name == game[f"{prefix}_PIT_P_NM"].strip()
        results[side] = {"name": name, "schedule_player_id": game[f"{prefix}_PIT_P_ID"]}
        for row in rows:
            result = row[result_index]["Text"]
            if result in ("승", "패"):
                key = "W" if result == "승" else "L"
                assert row[name_index]["Text"].strip() == game[f"{key}_PIT_P_NM"].strip()
                results[key] = {"name": row[name_index]["Text"].strip(), "schedule_player_id": game[f"{key}_PIT_P_ID"]}
    assert "W" in results and "L" in results
    assert results["W"]["schedule_player_id"] not in (results["away"]["schedule_player_id"], results["home"]["schedule_player_id"])
    print(json.dumps({"game_id": game_id, **results}, ensure_ascii=False))
