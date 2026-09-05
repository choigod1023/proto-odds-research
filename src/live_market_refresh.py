"""현재 발매 행만으로 picks_v2의 빈·낡은 시장 선택지를 경량 갱신한다.

전체 generate_v2는 과거 데이터와 선수 자료를 함께 읽어 운영 머신의 다른 수집기와
겹치면 OOM으로 종료될 수 있다. 이 경로는 live_odds의 발매 메타데이터와 DB의
확정 경기 기록으로 시장 판정·최근 폼을 갱신한다. 구조 모델/LLM 값은 새로 만들지 않는다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_decision import build_decision_snapshot  # noqa: E402
from bets import SEL_NAMES  # noqa: E402
from devig import market_probabilities  # noqa: E402
from game_dedup import deduplicate_game_sections  # noqa: E402
from prediction_ledger import (LedgerConflictError, LedgerCorruptionError,  # noqa: E402
                               LedgerLockTimeout, PredictionLedgerError)
from prediction_runtime import PredictionRuntime, kickoff_utc  # noqa: E402
from runtime_db import database_enabled, load_artifact, persist_artifact  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "docs" / "data" / "picks_v2.json"
LIVE_ODDS = ROOT / "docs" / "data" / "live_odds.json"
PREDICTION_LEDGER = ROOT / "data" / "raw" / "prediction_ledger" / "pregame.jsonl"
UNPLAYED = {"경기전", "", "-"}
LINE = re.compile(r"[-+]?\d+(?:\.\d+)?")
GAME_TIME = re.compile(r"(\d{1,2})\.(\d{1,2}).*?(\d{1,2}):(\d{2})")
KST = ZoneInfo("Asia/Seoul")


def _key(round_no, date, home, away) -> tuple[str, str, str, str]:
    return str(round_no), str(date or ""), str(home or ""), str(away or "")


def _aware_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _game_kickoff(game: dict, observed_at: datetime | None = None) -> datetime | None:
    """Parse the public Proto date as a naive KST kickoff for the ledger."""

    match = GAME_TIME.search(str(game.get("date") or ""))
    if not match:
        return None
    month, day, hour, minute = map(int, match.groups())
    fallback_year = (
        observed_at.astimezone(KST).year
        if observed_at is not None else datetime.now(KST).year
    )
    year = int(game.get("year") or fallback_year)
    if int(game.get("round") or 0) == 1 and month == 12:
        year -= 1
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def record_live_market_revisions(
    document: dict,
    observed_at: str,
    runtime: PredictionRuntime,
) -> dict[str, int]:
    """Persist every newly published pregame market decision before serving it.

    ``refresh_document`` remains a fileless document transform with DB disabled;
    in DB mode it reads confirmed history before building new decision snapshots.
    This operational step is deliberately strict: if a changed pregame decision
    cannot enter the append-only ledger, the caller must not publish that document.
    """

    observed = _aware_timestamp(observed_at)
    if observed is None:
        raise PredictionLedgerError("live market observed_at must include a timezone")
    counts = {"predictions": 0, "skipped": 0, "withheld": 0}
    candidates: list[tuple[dict, dict, str]] = []
    latest_by_event: dict[str, dict] = {}
    for record in runtime.records():
        if record.get("record_type") != "prediction":
            continue
        key = str(record.get("event_id") or "")
        current = latest_by_event.get(key)
        order = (_aware_timestamp(record.get("as_of")), int(record.get("ledger_sequence") or 0))
        current_order = (
            _aware_timestamp(current.get("as_of")), int(current.get("ledger_sequence") or 0)
        ) if current else (None, 0)
        if current is None or order > current_order:
            latest_by_event[key] = record

    # Validate the complete publish set before the first append. Expected stale
    # or malformed post-kickoff rows are withheld per game; a timestamp rollback
    # aborts the batch so old prices can never replace a newer published revision.
    for game in document.get("live") or []:
        snapshot = game.get("decision_snapshot") or {}
        if (game.get("prediction_status") == "withheld_unrecorded_live_revision"
                and game.get("live_revision_withheld_at") == observed_at):
            counts["withheld"] += 1
            continue
        if (
            game.get("status") != "경기전"
            or snapshot.get("as_of") != observed_at
        ):
            continue
        kickoff = _game_kickoff(game, observed)
        freeze_at = (_aware_timestamp(kickoff_utc(kickoff)) - timedelta(minutes=30)
                     if kickoff is not None else None)
        if freeze_at is None or observed >= freeze_at:
            game["추천"] = None
            game.pop("decision_snapshot", None)
            game.pop("prediction_record", None)
            game["prediction_status"] = "withheld_unrecorded_live_revision"
            game["_liveOddsChanged"] = True
            counts["withheld"] += 1
            continue
        event = str(snapshot.get("event_id") or "")
        existing = latest_by_event.get(event)
        if existing is not None and _aware_timestamp(existing.get("as_of")) > observed:
            raise PredictionLedgerError(
                "live market timestamp regressed behind the prediction ledger"
            )
        candidates.append((game, snapshot, kickoff_utc(kickoff)))

    touched: list[tuple[dict, dict]] = []
    batch_results = runtime.record_pregame_batch([
        {
            "game": game,
            "kickoff": kickoff_at,
            "market_observed_at": observed_at,
        }
        for game, _snapshot, kickoff_at in candidates
    ])
    for (game, snapshot, _kickoff_at), result in zip(candidates, batch_results):
        counts["predictions" if result is not None and result.appended else "skipped"] += 1
        record = result.record if result is not None else latest_by_event.get(
            str(snapshot.get("event_id") or "")
        )
        if (
            record is None
            or record.get("input_revision_hash") != snapshot.get("input_revision_hash")
        ):
            raise PredictionLedgerError("exact live market revision missing after ledger append")
        latest_by_event[str(snapshot.get("event_id") or "")] = record
        touched.append((game, record))

    latest_ui = runtime.ui_records()
    for game, record in touched:
        snapshot = game.get("decision_snapshot") or {}
        game["prediction_revision_id"] = record.get("snapshot_id")
        if snapshot.get("action") == "withhold":
            game.pop("prediction_record", None)
            game["prediction_status"] = "recorded_withhold"
            continue
        ui_record = latest_ui.get(snapshot.get("event_id"))
        if (
            ui_record is None
            or ui_record.get("prediction_snapshot_id") != record.get("snapshot_id")
        ):
            raise PredictionLedgerError("published live decision does not match exact ledger revision")
        game["prediction_record"] = ui_record
        game["prediction_status"] = "recorded_pregame"
    return counts


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


def _pick_drift(record: dict | None, options: list[dict],
                observed_at: str) -> dict | None:
    """고정된 사전 픽이 지금 시장 기준으로는 더 이상 유리한 쪽이 아닌지 본다.

    같은 마켓(같은 기준선)의 선택지 중 시장확률이 가장 높은 쪽이 고정 픽과
    다르면 드리프트로 본다. 이미 배팅한 사용자에게 "조건이 바뀌었다"만 알린다.
    """
    if not isinstance(record, dict) or not record.get("selection"):
        return None
    market = record.get("market")
    label = record.get("label") or ""
    pinned_selection = record.get("selection")
    same_market = [
        o for o in options
        if o.get("market") == market and (o.get("label") or "") == label
    ]
    if len(same_market) < 2 or not any(
        o.get("선택") == pinned_selection for o in same_market
    ):
        return None
    favored = max(same_market, key=lambda o: o.get("시장확률") or 0.0)
    if favored.get("선택") == pinned_selection:
        return None
    return {
        "pinned_selection": pinned_selection,
        "pinned_market": market,
        "pinned_label": label,
        "pinned_odds": record.get("odds"),
        "market_selection": favored.get("선택"),
        "market_odds": favored.get("배당"),
        "market_probability": round(favored.get("시장확률") or 0.0, 4),
        "observed_at": observed_at,
    }


def _option_signature(option: dict) -> tuple:
    """가격뿐 아니라 시장 종류와 기준점 변경도 하나의 revision으로 본다."""
    return (
        str(option.get("게임번호") or ""), str(option.get("market") or ""),
        str(option.get("label") or ""), option.get("line"),
        str(option.get("선택") or ""), option.get("배당"),
    )


def refresh_document(document: dict, live_odds: dict, *,
                     now: datetime | None = None) -> tuple[dict, int]:
    observed_at = str(live_odds.get("generated_at") or "")
    if not observed_at or not isinstance(live_odds.get("markets"), dict):
        return document, 0
    observed = _aware_timestamp(observed_at)
    use_database = database_enabled()
    # DB-off callers retain deterministic, fileless replay at the feed's clock.
    # Operational DB refreshes additionally reject feeds stale across kickoff.
    clock = now or (datetime.now(timezone.utc) if use_database else observed)
    if clock is not None and clock.tzinfo is None:
        clock = clock.replace(tzinfo=KST)
    decision_clock = max(observed, clock) if observed and clock else None
    form_provider = None
    existing = {
        _key(game.get("round"), game.get("date"), game.get("home"), game.get("away")): game
        for game in document.get("live") or []
    }
    grouped: dict[tuple, list[dict]] = {}
    for round_no, markets in live_odds["markets"].items():
        for row in (markets or {}).values():
            key = _key(round_no, row.get("date"), row.get("home"), row.get("away"))
            # 이미 목록에 있던 경기는 시작·종료 후 수집된 발매 행도 받아 둔다.
            # 과거 경기까지 새로 live에 되살리는 것은 막고, 기존의 빈 배당만 복구한다.
            if row.get("result") not in UNPLAYED and key not in existing:
                continue
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
                "year": (_aware_timestamp(observed_at) or datetime.now(timezone.utc))
                .astimezone(KST).year, "round": int(key[0]),
                "date": sample.get("date"), "league": sample.get("league"),
                "sport": sample.get("sport"), "home": sample.get("home"),
                "away": sample.get("away"), "no_model": True,
            }
            document.setdefault("live", []).append(game)
            existing[key] = game
        pregame = all(row.get("result") in UNPLAYED for row in rows)
        kickoff = _game_kickoff(game, observed)
        can_predict = bool(pregame and kickoff is not None and decision_clock is not None
                           and decision_clock < kickoff.replace(tzinfo=KST))
        old_options = game.get("options") or []
        # Kickoff 뒤에는 저장된 사전 가격·선택을 절대 덮어쓰지 않는다. 예전 코드는
        # 종료/진행 행의 배당으로 options를 교체한 뒤 원장을 제거해 라이브 화면이
        # 영원히 "재계산 대기"가 됐다. 기존 가격이 전혀 없을 때만 복구용으로 받는다.
        if not can_predict and old_options:
            continue
        old_signature = [_option_signature(row) for row in old_options]
        new_signature = [_option_signature(row) for row in options]
        old_lines = {
            (str(row.get("게임번호") or ""), str(row.get("market") or "")):
            (str(row.get("label") or ""), row.get("line"))
            for row in old_options
        }
        new_lines = {
            (str(row.get("게임번호") or ""), str(row.get("market") or "")):
            (str(row.get("label") or ""), row.get("line"))
            for row in options
        }
        line_changed = bool(old_options) and old_lines != new_lines
        target_status = "경기전" if pregame else (
            game.get("status") if game.get("status") not in {"", "경기전", "배당대기"}
            else "결과확인"
        )
        form_payload = None
        display_changed = False
        if use_database and can_predict:
            if form_provider is None:
                from team_form import DatabaseTeamForms

                sports = tuple(sorted({row.get("sport") for values in grouped.values()
                                       for row in values if row.get("sport")})) or None
                form_provider = DatabaseTeamForms(observed, clock, sports=sports)
            form_payload = form_provider.for_game(game, kickoff)
            # Display context is excluded from ledger_features and decision input
            # hashes. Never edit a saved revision's canonical form fields here.
            display_changed = game.get("team_form_display") != form_payload
            game["team_form_display"] = form_payload
        if old_signature == new_signature and game.get("status") == target_status:
            changed += int(display_changed)
            continue
        game.update({
            "status": target_status, "no_odds": False, "options": options,
            "선택지수": len(options),
        })
        if line_changed:
            game["market_line_changed"] = True
            game["market_line_changed_at"] = observed_at
            game["market_line_revision"] = {
                "before": [
                    {"game_no": key_[0], "market": key_[1], "label": value[0], "line": value[1]}
                    for key_, value in sorted(old_lines.items())
                ],
                "after": [
                    {"game_no": key_[0], "market": key_[1], "label": value[0], "line": value[1]}
                    for key_, value in sorted(new_lines.items())
                ],
            }
        pinned_snapshot = game.get("decision_snapshot") or {}
        pinned_record = game.get("prediction_record")
        already_pinned = bool(
            pregame
            and game.get("prediction_status") == "recorded_pregame"
            and isinstance(pinned_record, dict)
            and pinned_record.get("selection")
            and str(pinned_snapshot.get("as_of") or "") < observed_at
        )
        if already_pinned:
            # 첫 게시 때 원장에 고정된 사전 픽은 킥오프까지 바꾸지 않는다. 이미
            # 판매점에서 배팅한 사용자의 화면·정산 대상이 흔들리면 안 되기 때문이다.
            # 배당 숫자만 화면용으로 갱신하고, "지금 시장 기준이면 반대쪽이 유리"
            # 상황이면 pick_drift 로만 알린다.
            drift = _pick_drift(pinned_record, options, observed_at)
            if drift:
                game["pick_drift"] = drift
            else:
                game.pop("pick_drift", None)
        elif can_predict:
            if form_payload is not None:
                game.update(form_payload)
            game.update({
                "판단": "실시간 시장 기준", "추천": None,
                "해설": None, "해설기본": None,
                "설명메타": {"kind": "structured_ui", "affects_probability": False},
            })
            game["decision_snapshot"] = build_decision_snapshot(
                game, as_of=observed_at, built_at=observed_at,
                explanation_kind="structured_ui",
            )
            game.pop("pick_drift", None)
        else:
            # 경기 후 복구한 가격으로 사전 추천을 소급 생성하지 않는다.
            game.pop("decision_snapshot", None)
            game["추천"] = None
            game["prediction_status"] = (
                "withheld_unrecorded_live_revision" if pregame else "prediction_ledger_required")
            if pregame:
                game["live_revision_withheld_at"] = observed_at
                game["_liveOddsChanged"] = True
            game["odds_recovered_after_start"] = True
        changed += 1

    removed = deduplicate_game_sections(document)
    changed += removed
    if changed:
        document["generated_at"] = observed_at
        document["rounds"] = sorted({
            int(game.get("round")) for game in document.get("live") or []
            if str(game.get("round") or "").isdigit()
        })
        document["live_market_refresh"] = {
            "generated_at": observed_at, "games_changed": changed,
            "source": "current_proto_market_rows",
            "duplicate_reissues_removed": removed,
        }
    return document, changed


def refresh_once(live_odds: dict | None = None) -> int:
    try:
        document = load_artifact("picks_v2", PICKS)
        if live_odds is None:
            live_odds = load_artifact("live_odds", LIVE_ODDS)
        if document is None or live_odds is None:
            raise RuntimeError("required runtime artifact is missing from the database")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"경량 시장 판정 입력 실패: {type(exc).__name__}: {exc}")
        return 1
    document, changed = refresh_document(document, live_odds)
    if not changed:
        print("경량 시장 판정 변경 없음")
        return 0
    observed_at = str(live_odds.get("generated_at") or "")
    try:
        ledger_sync = record_live_market_revisions(
            document,
            observed_at,
            PredictionRuntime(PREDICTION_LEDGER),
        )
    except (LedgerCorruptionError, LedgerConflictError,
            LedgerLockTimeout, PredictionLedgerError) as exc:
        # A price can be shown only together with the immutable decision revision
        # that will later be settled. Retrying is safe because the ledger dedupes.
        print(f"경량 시장 판정 원장 실패: {type(exc).__name__}: {exc}")
        return 1
    document.setdefault("live_market_refresh", {})["ledger_sync"] = ledger_sync
    persist_artifact("picks_v2", document, PICKS)
    print(f"경량 시장 판정 {changed}경기 → runtime artifact picks_v2")
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
