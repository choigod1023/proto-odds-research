"""실시간 배당 직후 오늘 추천을 재계산하고 변경 이력을 남긴다."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import today_combo
from runtime_db import RuntimeDatabase, database_enabled

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "today_combo.json"
LEDGER = ROOT / "data" / "raw" / "recommendation_revisions.jsonl"


def _selection_key(row: dict) -> str:
    return "|".join(str(row.get(key, "")) for key in
                    ("round", "game_no", "market", "market_label", "sel"))


def recommendation_signature(payload: dict) -> dict:
    plans = []
    for plan in payload.get("plans") or []:
        if not plan.get("ok"):
            continue
        plans.append({"target": plan.get("target"),
                      "picks": [_selection_key(row) for row in plan.get("picks") or []]})
    recommendation = payload.get("recommendation") or {}
    return {"action": recommendation.get("action"),
            "recommended_target": recommendation.get("recommended_target"),
            "solo": _selection_key(payload["solo"]) if payload.get("solo") else None,
            "plans": plans}


def _reason(previous: dict | None, current: dict) -> str:
    if not previous:
        return "initial_snapshot"
    old = recommendation_signature(previous)
    new = recommendation_signature(current)
    if old["action"] != new["action"]:
        return "recommendation_status_changed"
    if old["plans"] != new["plans"] or old["solo"] != new["solo"]:
        return "live_market_pick_changed"
    return "prices_recalculated"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def refresh() -> dict:
    db = RuntimeDatabase() if database_enabled() else None
    previous = db.get_artifact("today_combo") if db else None
    if previous is None:
        if db is None:
            try:
                previous = json.loads(OUT.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = None
    payload = today_combo.build()
    signature = recommendation_signature(payload)
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    old_digest = (previous or {}).get("recommendation_revision")
    changed = digest != old_digest
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if changed:
        change = {"changed_at": now, "reason": _reason(previous, payload),
                  "previous_revision": old_digest, "revision": digest,
                  "previous": recommendation_signature(previous) if previous else None,
                  "current": signature}
        if db:
            db.append_events("recommendation_revisions", [change],
                             observed_at_key="changed_at")
        else:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(change, ensure_ascii=False) + "\n")
        payload["last_recommendation_change"] = change
    else:
        payload["last_recommendation_change"] = (previous or {}).get("last_recommendation_change")
    payload["recommendation_revision"] = digest
    payload["refreshed_at"] = now
    if db:
        db.store_artifact("today_combo", payload)
    else:
        _atomic_json(OUT, payload)
    return {"changed": changed, "revision": digest, "n_candidates": payload["n_candidates"]}


def main(argv: list[str]) -> int:
    loop = int(argv[argv.index("--loop") + 1]) if "--loop" in argv else 0
    while True:
        try:
            result = refresh()
            print(f"추천 재계산 후보 {result['n_candidates']}개 · "
                  f"{'변경' if result['changed'] else '유지'} · {result['revision'][:10]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"추천 재계산 실패: {type(exc).__name__}: {exc}", flush=True)
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
