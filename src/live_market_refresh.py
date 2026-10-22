"""현재 발매 행만으로 picks_v2의 빈·낡은 시장 선택지를 경량 갱신한다.

전체 generate_v2는 과거 데이터와 선수 자료를 함께 읽어 운영 머신의 다른 수집기와
겹치면 OOM으로 종료될 수 있다. 이 경로는 live_odds의 발매 메타데이터와 DB의
확정 경기 기록으로 시장 판정·최근 폼을 갱신한다. 구조 모델/LLM 값은 새로 만들지 않는다.
"""
from __future__ import annotations

import json
import gc
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_decision import build_decision_snapshot, event_id, selection_id, offer_id  # noqa: E402
from bets import SEL_NAMES, winner_index  # noqa: E402
from devig import market_probabilities  # noqa: E402
from game_dedup import deduplicate_game_sections  # noqa: E402
from prediction_ledger import (LedgerConflictError, LedgerCorruptionError,  # noqa: E402
                               LedgerLockTimeout, PredictionLedgerError)
from prediction_performance import performance_index
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
            or game.get("live_market_observed_at", snapshot.get("as_of")) != observed_at
        ):
            continue
        kickoff = _game_kickoff(game, observed)
        freeze_at = (_aware_timestamp(kickoff_utc(kickoff)) - timedelta(minutes=30)
                     if kickoff is not None else None)
        revision_time = _aware_timestamp(snapshot.get("as_of"))
        if freeze_at is None or revision_time is None or revision_time >= freeze_at:
            game["추천"] = None
            game.pop("decision_snapshot", None)
            game.pop("prediction_record", None)
            game["prediction_status"] = "withheld_unrecorded_live_revision"
            game["_liveOddsChanged"] = True
            counts["withheld"] += 1
            continue
        event = str(snapshot.get("event_id") or "")
        existing = latest_by_event.get(event)
        if existing is not None and (
            _aware_timestamp(existing.get("as_of")) > revision_time
            or _aware_timestamp(existing.get("market_observed_at") or existing.get("as_of")) > observed
        ):
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
    document["prediction_performance"] = performance_index(latest_ui)
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
                     now: datetime | None = None, player_document: dict | None = None) -> tuple[dict, int]:
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
    fixture_document = player_document
    fixtures_loaded = player_document is not None
    # Tiny independent KBO feed: do not wait for the all-sports player job.
    from kbo_pregame import current_document, display_info
    kbo_document = current_document() if use_database else {"games": []}
    if player_document is not None:
        kbo_document = player_document
    existing = {
        _key(game.get("round"), game.get("date"), game.get("home"), game.get("away")): game
        for game in document.get("live") or []
    }
    archived = {_key(game.get("round"), game.get("date"), game.get("home"), game.get("away"))
                for game in document.get("past") or []}
    grouped: dict[tuple, list[dict]] = {}
    for round_no, markets in live_odds["markets"].items():
        for row in (markets or {}).values():
            key = _key(round_no, row.get("date"), row.get("home"), row.get("away"))
            if key not in existing and key in archived:
                continue  # Never resurrect an archived game or replace its frozen record.
            # An outage can miss an entire day's non-football fixtures. Recover
            # recent identities even after they finish, but never invent a
            # pregame prediction from their final prices (can_predict below).
            # Keep historical rounds out: only today/yesterday in the real KST clock.
            if row.get("result") not in UNPLAYED and key not in existing:
                kickoff = _game_kickoff({**row, "round": round_no}, observed)
                if kickoff is None or decision_clock is None:
                    continue
                age_days = (decision_clock.astimezone(KST).date() - kickoff.date()).days
                if age_days not in (0, 1):
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
        # Official full-game results can restore display state, never a missing
        # pregame prediction. Half/handicap/total settlement is not game completion.
        result_labels = {"승패": {"홈승", "홈패"},
                         "승무패": {"홈승", "무승부", "홈패"},
                         "승①패": {"홈승", "①", "홈패"},
                         "승⑤패": {"홈승", "⑤", "홈패"}}
        recovered_result = bool(
            not can_predict and kickoff and decision_clock
            and decision_clock >= kickoff.replace(tzinfo=KST)
            and (game.get("odds_recovered_after_start") or not old_options)
            and any(row.get("result") in result_labels.get(row.get("market"), set())
                    for row in rows))
        result_changed = recovered_result and game.get("official_event_status") != "RESULT"
        if result_changed:
            game["official_event_status"] = "RESULT"
            game["official_event_status_source"] = "current_proto_full_game_result"
        # A recorded decision and its option vector are one revision. After
        # T-30 preserve both; before T-30 publish a new, ledger-backed revision.
        frozen = bool(kickoff and decision_clock and
                      decision_clock >= kickoff.replace(tzinfo=KST) - timedelta(minutes=30))
        if frozen and old_options:
            changed += int(result_changed)
            continue
        # Kickoff 뒤에는 저장된 사전 가격·선택을 절대 덮어쓰지 않는다. 예전 코드는
        # 종료/진행 행의 배당으로 options를 교체한 뒤 원장을 제거해 라이브 화면이
        # 영원히 "재계산 대기"가 됐다. 기존 가격이 전혀 없을 때만 복구용으로 받는다.
        if not can_predict and old_options:
            changed += int(result_changed)
            continue
        player_changed = False
        if can_predict and game.get("league") == "KBO":
            from pregame_player_capture import capture
            player_capture = capture(game, kbo_document,
                observed_at=decision_clock.isoformat(), kickoff=kickoff_utc(kickoff))
            if player_capture.get("status") == "captured":
                records = [r for r in kbo_document.get("games", [])
                           if r.get("game_id") == player_capture.get("source_game_id")]
                if len(records) == 1:
                    info = display_info(records[0])
                    player_changed = game.get("선발") != info
                    game["선발"] = info
                    game["research_player_inputs"] = player_capture
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
            game.get("status") if game.get("status") not in {None, "", "경기전", "배당대기"}
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
        snapshot = game.get("decision_snapshot") or {}
        selected = [row for row in old_options
                    if row.get("selection_id") == snapshot.get("selection_id")
                    and row.get("offer_id") == snapshot.get("offer_id")]
        broken_revision = (snapshot.get("action") == "market_reference" and
                           (not snapshot.get("selection_id") or len(selected) != 1 or
                            selected[0].get("시장확률") !=
                            (snapshot.get("probability") or {}).get("market")))
        if (old_signature == new_signature and game.get("status") == target_status
                and not broken_revision and not player_changed):
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
        if can_predict:
            # Only new/unpinned decisions capture context; never rewrite frozen inputs.
            if use_database and not fixtures_loaded:
                fixtures_loaded = True
                try:
                    from runtime_db import RuntimeDatabase
                    fixtures = []
                    for fixture_key, values in grouped.items():
                        sample = existing.get(fixture_key) or {
                            **values[0], "round": fixture_key[0],
                            "year": observed.astimezone(KST).year,
                        }
                        stamp = _game_kickoff(sample, observed)
                        if (stamp is not None and decision_clock < stamp.replace(tzinfo=KST)
                                and all(row.get("result") in UNPLAYED for row in values)):
                            fixtures.append((sample.get("league"), sample.get("home"),
                                             sample.get("away"), kickoff_utc(stamp)))
                    fixture_document = RuntimeDatabase().player_fixtures_document(fixtures)
                except (OSError, ValueError, sqlite3.Error):
                    fixture_document = None
            if (use_database or player_document is not None) and not player_changed:
                from pregame_player_capture import capture
                capture_clock = datetime.now(timezone.utc) if use_database and now is None else decision_clock
                game["research_player_inputs"] = capture(
                    game, fixture_document, observed_at=capture_clock.isoformat(),
                    kickoff=kickoff_utc(kickoff))
            if form_payload is not None:
                game.update(form_payload)
            game.update({
                "판단": "실시간 시장 기준", "추천": None,
                "해설": None, "해설기본": None,
                "설명메타": {"kind": "structured_ui", "affects_probability": False},
            })
            # A later lineup is a NEW decision, not evidence backdated into the
            # older odds snapshot. Keep the actual market clock in provenance.
            player_clock = _aware_timestamp((game.get("선발") or {}).get("updated_at"))
            revision_time = max(observed, player_clock) if player_clock else observed
            revision_stamp = revision_time.isoformat() if revision_time != observed else observed_at
            game["live_market_observed_at"] = observed_at
            game["decision_snapshot"] = build_decision_snapshot(
                game, as_of=revision_stamp, built_at=decision_clock.isoformat(),
                market_observed_at=observed_at,
                explanation_kind="structured_ui",
            )
            from form_probability_shadow import candidate
            # A separate prospective baseline, never fed back into pick ranking.
            shadow_clock = datetime.now(timezone.utc) if use_database and now is None else decision_clock
            game["validation_shadow"] = candidate(
                game, observed_at=shadow_clock.isoformat(), kickoff=kickoff_utc(kickoff))
            game.pop("pick_drift", None)
            game.pop("_liveOddsChanged", None)
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


def settle_live_market_results(live_odds: dict, runtime: PredictionRuntime) -> int:
    """Append exact-offer official outcomes without editing frozen predictions.

    Works even when a finished game has left picks_v2. No fuzzy matching, new
    predictions or price replacement; ambiguous duplicate result rows are skipped.
    """
    observed = _aware_timestamp(live_odds.get("generated_at"))
    if observed is None:
        raise PredictionLedgerError("settlement feed timestamp must include a timezone")
    candidates = {}
    for round_no, markets in (live_odds.get("markets") or {}).items():
        for row in markets.values():
            if row.get("result") in UNPLAYED:
                continue
            game = {**row, "round": int(round_no), "year": observed.astimezone(KST).year}
            kickoff = _game_kickoff(game, observed)
            if kickoff is None or kickoff.replace(tzinfo=KST) > observed:
                continue
            # Prevent a stale yearless feed from matching another year's fixture.
            if observed - kickoff.replace(tzinfo=KST) > timedelta(days=90):
                continue
            names = SEL_NAMES.get((row.get("market"), row.get("n_way")))
            if not names or len(names) != row.get("n_way"):
                continue
            void = row.get("result") in {"취소", "연기", "중단", "무효"}
            winner = winner_index(row.get("n_way"), row.get("result"))
            if not void and winner is None:
                continue
            line = None
            if row.get("market") in {"핸디캡", "언더오버", "전반핸디캡", "전반언더오버"}:
                match = LINE.search(str(row.get("label") or ""))
                if match is None:
                    continue
                line = float(match.group())
            for index, name in enumerate(names):
                option = {"market": row.get("market"), "label": row.get("label") or "",
                          "line": line, "선택": name, "게임번호": str(row.get("game_no"))}
                key = (event_id(game), selection_id(game, option), offer_id(game, option))
                candidates.setdefault(key, []).append((
                    "void" if void else "hit" if index == winner else "miss", row, round_no))
    pending = []
    for event, record in runtime.ui_records().items():
        key = (event, record.get("selection_id"), record.get("offer_id"))
        matches = candidates.get(key, [])
        if len(matches) != 1:
            continue
        result, row, round_no = matches[0]
        if record.get("result") == result:
            continue  # Full generator may already have settled with extra score metadata.
        pending.append(dict(
            event_id=event, outcome={"result": result, "selection_id": record["selection_id"],
                            "official_result": row.get("result")},
            settled_at=observed.isoformat(),
            source={"name": "proto_official", "round": round_no,
                    "game_no": row.get("game_no"), "game_date": row.get("date"),
                    "path": "live_odds"}))
    return sum(result.appended for result in runtime.settle_latest_batch(pending))


def refresh_once(live_odds: dict | None = None) -> int:
    started = time.monotonic()

    def stage(name: str) -> None:
        memory = ""
        # Linux ru_maxrss is KiB; do not mislabel macOS bytes or process peak as
        # total machine memory. This is diagnostic only, never an admission gate.
        if sys.platform.startswith("linux"):
            import resource
            memory = f" peak_rss_kib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}"
        print(f"경량 시장 판정 단계={name} 경과={time.monotonic() - started:.1f}s{memory}", flush=True)

    stage("load")
    try:
        document = load_artifact("picks_v2", PICKS)
        if live_odds is None:
            live_odds = load_artifact("live_odds", LIVE_ODDS)
        if document is None or live_odds is None:
            raise RuntimeError("required runtime artifact is missing from the database")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"경량 시장 판정 입력 실패: {type(exc).__name__}: {exc}")
        return 1
    stage("decision")
    document, changed = refresh_document(document, live_odds)
    observed_at = str(live_odds.get("generated_at") or "")
    try:
        runtime = PredictionRuntime(PREDICTION_LEDGER)
        stage("settlement")
        with runtime.ledger.verified_session():
            settled = settle_live_market_results(live_odds, runtime)
            stage("ledger")
            ledger_sync = record_live_market_revisions(
                document,
                observed_at,
                runtime,
            ) if changed else {"predictions": 0, "skipped": 0, "withheld": 0}
            ledger_sync["settlements"] = settled
            records = runtime.ui_records()
        attached = 0
        for game in [*(document.get("live") or []), *(document.get("past") or [])]:
            record = records.get(game.get("event_id") or event_id(game))
            if (record and record.get("result") != "pending"
                    and game.get("prediction_record") != record):
                game["prediction_record"] = record
                attached += 1
        if not changed and not settled and not attached:
            print("경량 시장 판정 변경 없음")
            return 0
    except (LedgerCorruptionError, LedgerConflictError,
            LedgerLockTimeout, PredictionLedgerError) as exc:
        # A price can be shown only together with the immutable decision revision
        # that will later be settled. Retrying is safe because the ledger dedupes.
        print(f"경량 시장 판정 원장 실패: {type(exc).__name__}: {exc}")
        return 1
    document.setdefault("live_market_refresh", {})["ledger_sync"] = ledger_sync
    # The immutable ledger is no longer needed during large JSON serialization.
    # Keep only the UI records already attached to the document, not its full
    # feature history alongside the document and the new serialized payload.
    del runtime, records
    gc.collect()
    stage("persist")
    persist_artifact("picks_v2", document, PICKS)
    stage("complete")
    print(f"경량 시장 판정 {changed}경기 → runtime artifact picks_v2")
    return 0


def main(argv: list[str]) -> int:
    loop = int(argv[argv.index("--loop") + 1]) if "--loop" in argv else 0
    while True:
        result = refresh_once()
        if not loop:
            return result
        time.sleep(loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
