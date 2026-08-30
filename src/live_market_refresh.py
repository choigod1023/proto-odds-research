"""현재 발매 행만으로 picks_v2의 빈·낡은 시장 선택지를 경량 갱신한다.

전체 generate_v2는 과거 데이터와 선수 자료를 함께 읽어 운영 머신의 다른 수집기와
겹치면 OOM으로 종료될 수 있다. 이 경로는 live_odds의 작은 발매 메타데이터만 읽고
시장 확률·판정 계약을 갱신한다. 구조 모델/LLM 값은 새로 만들지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_decision import build_decision_snapshot  # noqa: E402
from bets import SEL_NAMES  # noqa: E402
from devig import market_probabilities  # noqa: E402
from runtime_db import RuntimeDatabase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "docs" / "data" / "picks_v2.json"
LIVE_ODDS = ROOT / "docs" / "data" / "live_odds.json"
UNPLAYED = {"경기전", "", "-"}
LINE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _key(round_no, date, home, away) -> tuple[str, str, str, str]:
    return str(round_no), str(date or ""), str(home or ""), str(away or "")


def _options(rows: list[dict]) -> list[dict]:
    options = []
    for row in sorted(rows, key=lambda value: int(value.get("game_no") or 0)):
        odds = [float(value) for value in row.get("odds") or []]
        family = row.get("market")
        names = SEL_NAMES.get((family, len(odds)))
        if not names or len(names) != len(odds) or any(value <= 1 for value in odds):
            continue
        probabilities = market_probabilities(odds)
        match = LINE.search(str(row.get("label") or ""))
        line = float(match.group()) if match and family in {
            "언더오버", "핸디캡", "전반언더오버", "전반핸디캡",
        } else None
        for index, (name, price, probability) in enumerate(zip(names, odds, probabilities)):
            options.append({
                "market": family, "n_way": len(odds),
                "label": row.get("label") or "", "line": line,
                "선택": name, "배당": round(price, 2),
                "시장확률": round(probability, 4), "모델확률": None,
                "최종확률": round(probability, 4), "확률근거": "shin_market_live",
                "AI반영": False, "AI잔차": None,
                "게임번호": str(row.get("game_no")), "적중": None,
            })
    return options


def refresh_document(document: dict, live_odds: dict) -> tuple[dict, int]:
    observed_at = str(live_odds.get("generated_at") or "")
    if not observed_at or not isinstance(live_odds.get("markets"), dict):
        return document, 0
    existing = {
        _key(game.get("round"), game.get("date"), game.get("home"), game.get("away")): game
        for game in document.get("live") or []
    }
    grouped: dict[tuple, list[dict]] = {}
    for round_no, markets in live_odds["markets"].items():
        for row in (markets or {}).values():
            if row.get("result") not in UNPLAYED:
                continue
            key = _key(round_no, row.get("date"), row.get("home"), row.get("away"))
            grouped.setdefault(key, []).append(row)

    changed = 0
    for key, rows in grouped.items():
        options = _options(rows)
        if not options:
            continue
        game = existing.get(key)
        if game is None:
            sample = rows[0]
            game = {
                "year": datetime.now(timezone.utc).year, "round": int(key[0]),
                "date": sample.get("date"), "league": sample.get("league"),
                "sport": sample.get("sport"), "home": sample.get("home"),
                "away": sample.get("away"), "no_model": True,
            }
            document.setdefault("live", []).append(game)
            existing[key] = game
        old_signature = [(row.get("게임번호"), row.get("배당")) for row in game.get("options") or []]
        new_signature = [(row.get("게임번호"), row.get("배당")) for row in options]
        if old_signature == new_signature and game.get("status") == "경기전":
            continue
        game.update({
            "status": "경기전", "no_odds": False, "options": options,
            "판단": "실시간 시장 기준", "추천": None,
            "선택지수": len(options), "해설": None, "해설기본": None,
            "설명메타": {"kind": "structured_ui", "affects_probability": False},
        })
        game["decision_snapshot"] = build_decision_snapshot(
            game, as_of=observed_at, built_at=observed_at,
            explanation_kind="structured_ui",
        )
        changed += 1

    if changed:
        document["generated_at"] = observed_at
        document["rounds"] = sorted({
            int(game.get("round")) for game in document.get("live") or []
            if str(game.get("round") or "").isdigit()
        })
        document["live_market_refresh"] = {
            "generated_at": observed_at, "games_changed": changed,
            "source": "current_proto_market_rows",
        }
    return document, changed


def refresh_once() -> int:
    try:
        document = json.loads(PICKS.read_text(encoding="utf-8"))
        live_odds = json.loads(LIVE_ODDS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"경량 시장 판정 입력 실패: {type(exc).__name__}: {exc}")
        return 1
    document, changed = refresh_document(document, live_odds)
    if not changed:
        print("경량 시장 판정 변경 없음")
        return 0
    RuntimeDatabase().store_artifact("picks_v2", document)
    temporary = PICKS.with_suffix(PICKS.suffix + ".market.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(PICKS)
    print(f"경량 시장 판정 {changed}경기 → {PICKS}")
    return 0


def main(argv: list[str]) -> int:
    loop = int(argv[argv.index("--loop") + 1]) if "--loop" in argv else 0
    while True:
        refresh_once()
        if not loop:
            return 0
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
