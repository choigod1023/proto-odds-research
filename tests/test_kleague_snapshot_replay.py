import csv
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import kleague_snapshot_replay as replay
import snapshot
from ai_decision import event_id, offer_id, selection_id
from devig import multiplicative, shin
from featured_pick import VERSION
from runtime_db import RuntimeDatabase


FIXTURE = dict(year=2026, round=130, sport="sc", league="K리그2",
               home="충북청주", away="서울이랜", date="11.06(금) 19:30")
MARKETS = (
    ("001", "승무패", "", [2.0, 3.2, 3.1], "홈승"),
    ("002", "핸디캡", "H -1.0", [3.5, 3.3, 1.8], "핸디무"),
    ("003", "언더오버", "U 2.5", [1.74, 2.1], "오버"),
    ("004", "핸디캡", "H -2.0", [5.0, 3.8, 1.4], "핸디패"),
)


def _inputs(league="K리그2"):
    fixture = dict(FIXTURE, league=league)
    price_rows, result_rows = [], {}
    for number, market, label, odds, result in MARKETS:
        price_rows.append(dict(fixture, game_no=number, market_family=market,
                               market_label=label, n_way=len(odds), date_text=FIXTURE["date"],
                               odds=",".join(map(str, odds)), result="경기전"))
        # Different closing odds prove the replay only uses the receipt prices.
        result_rows[number] = dict(fixture, game_no=number, market=market, label=label,
                                   n_way=len(odds), result=result, odds=[1.01] * len(odds))
    receipt = dict(year=2026, round=130, received_at="2026-11-06T09:59:58.123456+00:00",
                   timestamp_basis="http_response_body_completed", body_sha256="a" * 64, rows=price_rows)
    results = dict(generated_at="2026-11-06T13:00:00+00:00", markets={"130": result_rows})
    option = {"게임번호": "002", "market": "핸디캡", "label": "H -1.0", "line": -1.0, "선택": "핸디무"}
    pick = dict(version=VERSION, market=option["market"], label=option["label"], line=-1.0,
                selection=option["선택"], game_no="002", odds=3.3, settlement_rule="handicap_3way",
                offer_id=offer_id(fixture, option), selection_id=selection_id(fixture, option))
    prediction = dict(record_type="prediction", event_id=event_id(fixture), snapshot_id="frozen",
                      kickoff="2026-11-06T10:30:00+00:00", as_of="2026-11-06T09:59:59+00:00",
                      market_observed_at="2026-11-06T09:59:59+00:00",
                      captured_at="2026-11-06T09:59:59.9+00:00",
                      features={"fixture": fixture}, predictions={"featured_pick": pick})
    return receipt, prediction, results


@pytest.mark.parametrize("sqlite", [False, True])
def test_snap_preserves_each_body_completion_and_legacy_output(tmp_path, monkeypatch, sqlite):
    database_path = tmp_path / "runtime.sqlite3"
    if sqlite:
        monkeypatch.setenv("PROODD_DB_PATH", str(database_path))
    else:
        monkeypatch.delenv("PROODD_DB_PATH", raising=False)
    monkeypatch.setattr(snapshot, "OUT", tmp_path)
    monkeypatch.setattr(snapshot, "CH_FILE", tmp_path / "changes.csv")
    monkeypatch.setattr(snapshot.time, "sleep", lambda *_: None)
    monkeypatch.setattr(snapshot, "get_master_seq", lambda *_: "42")

    game_rows = []
    for number, market, label, odds, _ in MARKETS:
        prices = "".join(f'<li class="a9">{price:.2f}</li>' for price in odds)
        tag = "un" if market == "언더오버" else "hm"
        game_rows.append(f"""<ul>
            <li class="a1">{number}</li><li class="a2">11.06(금) 19:30</li>
            <li class="a3 sc">축구</li><li class="a4">K리그2</li>
            <li class="{tag}">{label}</li>
            <li class="a6">충북청주</li><li class="a8">서울이랜</li>
            {prices}<li>경기전</li></ul>""")
    body = '<div class="gameinfo">' + "".join(game_rows) + "</div>"
    received = {130: "2026-11-05T23:59:59.987654+00:00", 131: "2026-11-06T00:00:02.123456+00:00"}
    now = "2026-11-05T23:58:00+00:00"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=timezone.utc):
            return datetime.fromisoformat(now).astimezone(tz)

    class Response:
        text = body

        def __init__(self, rnd):
            self.rnd = rnd

        def raise_for_status(self):
            pass

        @property
        def content(self):
            nonlocal now
            now = received[self.rnd]
            return body.encode("utf-8")

    monkeypatch.setattr(snapshot, "datetime", Clock)
    monkeypatch.setattr(snapshot, "_session", lambda: SimpleNamespace(
        get=lambda url, *, params, timeout: Response(params["game_round"])))
    original_decode = snapshot._decode

    def decode(response):
        nonlocal now
        now = "2026-11-06T00:10:00+00:00"
        return original_decode(response)

    monkeypatch.setattr(snapshot, "_decode", decode)
    snapshot.snap(2026, [130, 131], kleague_receipts=True)
    paths = sorted(tmp_path.glob("kleague_odds_receipts_*.jsonl"))
    receipts = list(replay._receipts(paths, database_path if sqlite else None))
    assert {row["round"]: row["received_at"] for row in receipts} == received
    assert all(row["body_sha256"] == hashlib.sha256(body.encode()).hexdigest() for row in receipts)
    assert all(row["timestamp_basis"] == "http_response_body_completed" for row in receipts)

    legacy = {rnd: datetime.fromisoformat(instant).isoformat(timespec="seconds")
              for rnd, instant in received.items()}
    bodies = None
    if sqlite:
        assert not paths
        assert not (tmp_path / snapshot.RECEIPT_BODY_DIR).exists()
        database = RuntimeDatabase(database_path)
        assert {r["round"]: r["observed_at"] for r in database.latest_odds()} == legacy
    else:
        assert len(paths) == 2
        bodies = tmp_path / snapshot.RECEIPT_BODY_DIR
        raw = snapshot.receipt_body_slot(bodies, receipts[0]["body_sha256"])
        assert gzip.decompress(raw.read_bytes()) == body.encode("utf-8")
        instant = datetime.fromisoformat(receipts[0]["received_at"])
        assert replay._receipt_evidence(receipts[0], instant, bodies) == "raw_verified"
        tampered = deepcopy(receipts[0])
        tampered["rows"][0]["odds"] = "2.00,4.00,3.10"
        assert replay._receipt_evidence(tampered, instant, bodies) == "raw_row_mismatch"
        for day, rnd in (("20261105", 130), ("20261106", 131)):
            with (tmp_path / f"odds_timeseries_{day}.csv").open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                assert reader.fieldnames == snapshot.FIELDS
                assert next(reader)["ts"] == legacy[rnd]

    _, prediction, results = _inputs()
    report = replay.compare(receipts, [prediction], [results], bodies=bodies)
    assert report["eligible_fixtures"] == 1
    assert report["rows"][0]["receipt_evidence"] == ("parsed_only" if sqlite else "raw_verified")

    # A default run writes only the compatible odds path.
    snapshot.snap(2026, [130])
    assert list(replay._receipts(paths, database_path if sqlite else None)) == receipts


def test_local_raw_bodies_use_fixed_slots_and_enforce_size(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "OUT", tmp_path)
    monkeypatch.setattr(snapshot, "RECEIPT_BODY_SLOTS", 2)
    monkeypatch.setattr(snapshot, "MAX_RECEIPT_BODY", 20)
    for value in range(10):
        body = str(value).encode()
        snapshot._save_receipt_body(body, hashlib.sha256(body).hexdigest())
    bodies = tmp_path / snapshot.RECEIPT_BODY_DIR
    assert len(list(bodies.iterdir())) == 2
    before = {path: path.read_bytes() for path in bodies.iterdir()}
    oversized = b"x" * 21
    snapshot._save_receipt_body(oversized, hashlib.sha256(oversized).hexdigest())
    assert {path: path.read_bytes() for path in bodies.iterdir()} == before
    assert snapshot.main(["snapshot.py", "--kleague-receipts", "--loop", "900"]) == 2


@pytest.mark.parametrize("league", ["K리그1", "K리그2"])
def test_compare_uses_exact_frozen_offer_and_shin_on_both_three_way_markets(league):
    receipt, prediction, result = _inputs(league)
    report = replay.compare([receipt], [prediction], [result])
    assert report["eligible_fixtures"] == 1
    assert report["rows"][0]["receipt_evidence"] == "parsed_only"
    assert report["hit_rates"] == {"frozen_pick": 1.0, "same_offer_market_favorite": 0.0}
    products = report["rows"][0]["products"]
    assert len(products) == 4  # Both verified handicap lines remain distinct.
    for row, (_, _, _, odds, _) in zip(products, MARKETS):
        expected = shin(odds) if len(odds) == 3 else multiplicative(odds)
        assert list(row["probabilities"].values()) == pytest.approx(expected)
        assert list(row["odds"].values()) == odds


@pytest.mark.parametrize(("mutation", "reason"), [
    ("legacy", "no_verified_pregame_receipt"),
    ("late_body", "no_verified_pregame_receipt"),
    ("late_pick", "invalid_frozen_timing"),
    ("wrong_price", "frozen_offer_or_price_mismatch"),
    ("wrong_offer", "frozen_offer_or_price_mismatch"),
    ("wrong_round", "official_result_missing_or_ambiguous"),
    ("wrong_result", "official_result_missing_or_ambiguous"),
    ("wrong_line", "official_result_missing_or_ambiguous"),
    ("duplicate_result", "official_result_missing_or_ambiguous"),
    ("duplicate_product", "incomplete_or_ambiguous_three_markets"),
    ("missing_total", "incomplete_or_ambiguous_three_markets"),
])
def test_replay_fails_closed(mutation, reason):
    receipt, prediction, result = _inputs()
    pick = prediction["predictions"]["featured_pick"]
    bucket = result["markets"]["130"]
    if mutation == "legacy":
        receipt["ts"] = receipt.pop("received_at")
        receipt.pop("timestamp_basis")
    elif mutation == "late_body":
        receipt["received_at"] = "2026-11-06T10:00:00.000001+00:00"
    elif mutation == "late_pick":
        prediction["captured_at"] = "2026-11-06T10:00:00+00:00"
    elif mutation == "wrong_price":
        pick["odds"] = 3.4
    elif mutation == "wrong_offer":
        pick["offer_id"] = "other-round-offer"
    elif mutation == "wrong_round":
        result["markets"] = {"131": bucket}
    elif mutation == "wrong_result":
        bucket["002"]["result"] = "무승부"
    elif mutation == "wrong_line":
        bucket["002"]["label"] = "H +1.0"
    elif mutation == "duplicate_result":
        bucket["copy"] = bucket["002"].copy()
    elif mutation == "duplicate_product":
        receipt["rows"].append(dict(receipt["rows"][1], game_no="005"))
    else:
        receipt["rows"].pop(2)
    report = replay.compare([receipt], [prediction], [result])
    assert report["eligible_fixtures"] == 0
    assert report["hit_rates"] is None
    assert report["counts"][reason] == 1


def test_never_merge_markets_across_responses_or_fall_back_to_older_price():
    first, prediction, result = _inputs()
    last = deepcopy(first)
    last.update(received_at="2026-11-06T09:59:58.223456+00:00", body_sha256="b" * 64)
    last["rows"] = last["rows"][:2]
    report = replay.compare([first, last], [prediction], [result])
    assert report["counts"]["incomplete_or_ambiguous_three_markets"] == 1
    assert report["hit_rates"] is None


def test_official_void_excluded_from_hit_rates():
    receipt, prediction, result = _inputs()
    result["markets"]["130"]["002"]["result"] = "무효"
    report = replay.compare([receipt], [prediction], [result])
    assert report["eligible_fixtures"] == 1
    assert report["decided_fixtures"] == 0
    assert report["hit_rates"] is None


def test_conflicting_terminal_results_across_saved_documents_are_excluded():
    receipt, prediction, result = _inputs()
    conflicting = deepcopy(result)
    conflicting["generated_at"] = "2026-11-06T14:00:00+00:00"
    conflicting["markets"]["130"]["002"]["result"] = "핸디승"
    report = replay.compare([receipt], [prediction], [result, conflicting])
    assert report["counts"]["official_result_missing_or_ambiguous"] == 1
    assert report["hit_rates"] is None
