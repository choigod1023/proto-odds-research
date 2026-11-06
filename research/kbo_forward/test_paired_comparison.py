"""Synthetic forward receipts and ledger exports only."""
import base64
from copy import deepcopy
from datetime import date, timedelta
import gzip
import json
import sys
from types import SimpleNamespace

import pytest

from research.kbo_forward import features, official, paired_comparison as pair, prices, runner
from research.kbo_forward.lineup_m0 import revision_anchor
from research.kbo_forward.test_features import data
from research.kbo_forward.test_lineup_revisions import archive, ordered, GAME, NOW, ROUND, START


def write_export(path, records):
    previous = None
    lines = []
    for sequence, record in enumerate(records, start=1):
        unsigned = record | {"ledger_sequence": sequence, "previous_record_hash": previous}
        previous = pair.ledger.digest(pair.ledger.canonical(unsigned))
        lines.append(pair.ledger.canonical(unsigned | {"record_hash": previous}) + b"\n")
    raw = b"".join(lines)
    path.write_bytes(gzip.compress(raw) if path.suffix == ".gz" else raw)


def operating_record(row, label):
    fixture = {"sport": "bs", "league": "KBO", "year": 2026, "round": 127,
               "home": "삼성 라이온즈", "away": "KIA", "date": "10.26(월) 19:00"}
    options = []
    for name, side in pair.SIDES.items():
        option = {"market": "승패", "label": "", "line": None, "선택": name, "게임번호": "796"}
        options.append({"selection_id": pair.selection_id(fixture, option),
                        "offer_id": pair.offer_id(fixture, option),
                        "market": "승패", "label": "", "line": None, "selection": name,
                        "odds": row["market"]["odds"][side]})
    pick = next(o for o in options if o["selection"] == label)
    return {"record_type": "prediction", "schema_version": "pregame-prediction-ledger-v1",
            "snapshot_id": "frozen", "event_id": pair.event_id(fixture),
            "features": {"fixture": fixture, "options": options},
            "predictions": {"featured_pick": pick | {"version": pair.FEATURED_VERSION, "game_no": "796",
                                                     "settlement_rule": "official_winner"}},
            "kickoff": START.isoformat(), "market_observed_at": NOW.isoformat(),
            "as_of": NOW.isoformat(), "captured_at": NOW.isoformat()}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setitem(GAME, "HOME_NM", "삼성")
    monkeypatch.setitem(GAME, "AWAY_NM", "KIA")
    archive(tmp_path)
    report = pair.replay(tmp_path, START + timedelta(days=1))
    row, = report["rows"]
    after = row["after"]
    root = tmp_path / "official"
    bundles = root / "verified-bundles"
    bundles.mkdir()
    receipt_log = [json.loads(line) for line in (root / "receipts.jsonl").read_bytes().splitlines()]

    def response(evidence):
        rec, = [rec for rec in receipt_log if rec["url"] == evidence["url"]
                and rec["finished_at"] == evidence["received_at"]]
        filename = "bodies/" + rec["sha256"] + ".bin"
        return {"receipt": rec | {"body_file": filename},
                "body_base64": base64.b64encode((root / filename).read_bytes()).decode("ascii")}

    def bundled(body):
        raw = pair.ledger.canonical(body)
        sha = pair.ledger.digest(raw)
        path = bundles / (sha + ".json.gz")
        path.write_bytes(gzip.compress(raw))
        ref = {"archived_canonical_bundle_sha256": sha, "relative_file": path.relative_to(root).as_posix()}
        return {"uri": official.SCHEDULE, "received_at": after["lineup"]["received_at"], "body": ref,
                "sha256": pair.ledger.digest(pair.ledger.canonical(ref))}

    _, roster, history = data()
    # Retarget the synthetic history to the October #612 fixture.
    def retarget(value):
        if isinstance(value, dict):
            return {k: ((date.fromisoformat(v) - timedelta(days=9)).isoformat()
                        if k in {"date", "through_date", "from_date"}
                        else (NOW - timedelta(days=1)).isoformat() if k == "received_at" else retarget(v))
                    for k, v in value.items()}
        return [retarget(v) for v in value] if isinstance(value, list) else value

    history = retarget(history)
    for side in ("home", "away"):
        starter = after["starters"][side]["player_id"]
        for game in history["seed"]["games"] + history["games"]:
            if roster[side]["starter"] in game["pitchers"]:
                game["pitchers"][starter] = game["pitchers"].pop(roster[side]["starter"])
        roster[side] = {"starter": starter,
                        "hitters": [side + str(int(p["name"][4:]) - 1) for p in after["batting_order"][side]]}

    # Raw box/identity parsing have their own verifier regressions. Here the
    # actual offline bundle/clock, feature reconstruction and model checks run.
    monkeypatch.setattr(official.OfficialClient, "verify_history_stats", lambda *a: True)
    monkeypatch.setattr(official.OfficialClient, "resolve_identity",
                        lambda self, name, team, *a, **kw:
                        (("home" if team == "삼성" else "away") + str(int(name[4:]) - 1), []))
    schedule = response(after["schedule"])
    evidence = {"announcement": bundled({"responses": [schedule, response(after["lineup"])]}),
                "history": bundled({"responses": [schedule], "normalized_history": history})}
    offer = row["market"]
    quote = offer["observations"][0]
    price_sha = quote["detail"]["raw_sha256"]
    body = json.loads((tmp_path / "prices/price-evidence" / (price_sha + ".body")).read_bytes())
    evidence["odds"] = {"uri": quote["detail"]["url"], "body": body,
                        "sha256": pair.ledger.digest(pair.ledger.canonical(body)),
                        "received_at": quote["detail"]["received_at"], "raw_sha256": price_sha,
                        "round_listing_sha256": quote["listing"]["raw_sha256"]}
    started = NOW + timedelta(minutes=8)
    event = row["event"]
    payload = {
        "event": event, "row": features.build_row(event, roster, history, started.isoformat()),
        "market": {k: offer[k] for k in ("kind", "market_id", "settlement_rule", "odds",
                                          "close_at", "round_close_at", "source_product")},
        "evidence": evidence,
        "announcement": {"lineup_confirmed": True, "pitcher_announcement_confirmed": True,
                         "basis": "official_confirmed_pregame_announcement", "roster": roster},
    }
    path = tmp_path / "paired-ledger.jsonl"
    capture = pair.ledger.capture(payload, path, clock=lambda: started)
    q = capture["models"]["baseline"]["probabilities_2way"]
    label = "원정" if q["home"] >= q["away"] else "홈"
    operating = operating_record(row, label)
    export = tmp_path / "operating.jsonl.gz"
    write_export(export, [operating])
    return report, capture, operating, export


def test_cli_pairs_real_replay_and_frozen_model_with_exact_existing_pick(tmp_path, sources, monkeypatch, capsys):
    _, _, operating, export = sources
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "must-not-open.sqlite"))
    monkeypatch.setattr(sys, "argv", ["paired_comparison", "--state", str(tmp_path),
                                      "--operating-ledger", str(export),
                                      "--as-of", (START + timedelta(days=1)).isoformat()])
    pair.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "compared"
    assert result["paired_offers"] == result["paired_games"] == 1
    row, = result["rows"]
    existing, revised = (row["arms"][k] for k in ("existing_pick", "revised_m0"))
    assert existing["pick"] == pair.SIDES[operating["predictions"]["featured_pick"]["selection"]]
    assert existing["pick"] != revised["pick"]
    assert {existing["grade"], revised["grade"]} == {"hit", "miss"}
    assert sum(m["hits"] for m in result["metrics"].values()) == 1
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_no_fallback_to_earlier_pick_and_t30_is_strict(tmp_path, sources):
    report, capture, original, export = sources
    later = deepcopy(original)
    later.update(snapshot_id="newer", as_of=(NOW + timedelta(minutes=10)).isoformat(),
                 captured_at=(NOW + timedelta(minutes=10)).isoformat(), predictions={})
    write_export(export, [original, later])
    result = pair.compare(report, pair.read_operating_export(export), [capture], tmp_path)
    assert result["exclusion_counts"] == {"operating_frozen_featured_pick_missing": 1}
    boundary = (START - pair.CUTOFF).isoformat()
    later.update(as_of=boundary, captured_at=boundary)
    write_export(export, [later])
    result = pair.compare(report, pair.read_operating_export(export), [capture], tmp_path)
    assert result["exclusion_counts"] == {"operating_pre_t30_revision_missing": 1}


def test_exact_offer_and_complete_roster_prices_required(tmp_path, sources):
    report, capture, operating, export = sources
    operating["predictions"]["featured_pick"]["game_no"] = "797"
    write_export(export, [operating])
    result = pair.compare(report, pair.read_operating_export(export), [capture], tmp_path)
    assert result["exclusion_counts"] == {"operating_pick_different_offer_or_terms": 1}
    operating["predictions"]["featured_pick"]["game_no"] = "796"
    operating["features"]["options"][0]["odds"] += 0.01
    write_export(export, [operating])
    result = pair.compare(report, pair.read_operating_export(export), [capture], tmp_path)
    assert result["exclusion_counts"] == {"operating_offer_roster_or_odds_mismatch": 1}


def test_missing_sources_unsupported_products_and_early_frozen_m0(tmp_path, sources):
    report, capture, _, export = sources
    result = pair.compare(report, None, [], tmp_path)
    assert result["exclusion_counts"] == {"operating_ledger_export_missing": 1, "m0_frozen_capture_missing": 1}
    assert result["metrics"]["revised_m0"]["unit_stake_paper_roi"] is None
    early = capture | {"received_at": NOW.isoformat(), "frozen_at": NOW.isoformat()}
    result = pair.compare(report, pair.read_operating_export(export), [early], tmp_path)
    assert result["exclusion_counts"] == {"m0_frozen_before_revised_lineup": 1}
    report["rows"][0]["market"]["kind"] = "full_game_total"
    result = pair.compare(report, None, [], tmp_path)
    assert result["exclusion_counts"] == {"m0_prospective_product_unsupported": 1}


def test_same_product_refund_and_latest_revision_only(tmp_path, sources):
    report, capture, _, export = sources
    row, = report["rows"]
    row["official_result"]["away_score"] = row["official_result"]["home_score"]
    earlier = deepcopy(row)
    earlier["after"]["lineup"]["received_at"] = (NOW + timedelta(minutes=6)).isoformat()
    report["rows"].append(earlier)
    result = pair.compare(report, pair.read_operating_export(export), [capture], tmp_path)
    assert result["unique_eligible_offers"] == result["paired_offers"] == 1
    for metric in result["metrics"].values():
        assert metric["refunds"] == 1 and metric["hit_rate"] is None
        assert metric["unit_stake_paper_roi"] == 0


def test_incomplete_or_tampered_operating_export_fails_closed(tmp_path, sources):
    _, _, operating, _ = sources
    export = tmp_path / "operating.jsonl"
    write_export(export, [operating])
    raw = export.read_bytes()
    export.write_bytes(raw.replace(b'"snapshot_id":"frozen"', b'"snapshot_id":"edited"'))
    with pytest.raises(ValueError, match="invalid record hash"):
        pair.read_operating_export(export)
    export.write_bytes(raw[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        pair.read_operating_export(export)


def test_opt_in_runner_freezes_revised_m0_separately_and_retries(tmp_path, sources):
    report, baseline, _, export = sources
    row, = report["rows"]
    root = tmp_path / "official"
    records = [json.loads(line) for line in (root / "receipts.jsonl").read_bytes().splitlines()]
    receipt = next(r for r in records if r["finished_at"] == row["after"]["lineup"]["received_at"])
    schedule = next(r for r in records if r["finished_at"] == row["after"]["schedule"]["received_at"])
    game = {"source": GAME, "receipt": schedule}
    payload = deepcopy(baseline["input"])
    price = payload["evidence"].pop("odds")
    price_root = tmp_path / "prices/price-evidence"
    raw_price = price_root / (price["raw_sha256"] + ".body")
    quote_evidence = {"uri": price["uri"], "received_at": price["received_at"],
                      "sha256": price["raw_sha256"], "body_file": str(raw_price),
                      "round_listing_sha256": price["round_listing_sha256"]}
    quotes, _ = prices.parse_detail(raw_price.read_bytes(), ROUND, [row["event"]],
                                    price["received_at"], quote_evidence)
    payload.pop("market")

    class Source:
        snapshot_calls = 0

        def __init__(self):
            self.root, self.records = root, records

        def schedule(self, days):
            return {"games": [game]}

        def fetch_lineup(self, source):
            return None, receipt

        def snapshot(self, game):
            self.snapshot_calls += 1
            if self.snapshot_calls == 1:
                return {"source_verified": False, "missing": ["verified_history_coverage_incomplete"]}
            return {"source_verified": True, "lineup_receipt": receipt, "payload": payload}

        def result(self, game):
            return {"verified": False}

    source = Source()
    now = NOW + timedelta(minutes=8)
    price_fn = lambda *a: {"quotes": quotes, "requests": [], "excluded": []}
    path = tmp_path / "paired-ledger.jsonl"
    runner.run_once(tmp_path, source, price_fn, clock=lambda: now)
    before = path.read_bytes()
    assert source.snapshot_calls == 0
    assert not (tmp_path / pair.CAPTURE_DIR).exists()
    first, second, retry = [
        runner.run_once(tmp_path, source, price_fn, clock=lambda: now, watch_lineup_revisions=True)
        for _ in range(3)
    ]
    assert [r["revised_m0_captured_this_run"] for r in (first, second, retry)] == [0, 1, 0]
    assert first["lineup_revision_excluded"][0]["reasons"] == ["verified_history_coverage_incomplete"]
    assert source.snapshot_calls == 2
    assert path.read_bytes() == before
    late = runner.run_once(tmp_path, source, price_fn, clock=lambda: START - timedelta(minutes=29),
                           watch_lineup_revisions=True)
    assert late["revised_m0_captured_this_run"] == 0 and source.snapshot_calls == 2
    result = pair.compare(report, pair.read_operating_export(export), [], tmp_path)
    assert result["paired_offers"] == 1
    assert result["rows"][0]["arms"]["revised_m0"]["source"] == "lineup_revision_ledger"
    capture_file, = (tmp_path / pair.CAPTURE_DIR).glob("*.jsonl")
    capture_file.write_bytes(capture_file.read_bytes()[:-1])
    result = pair.compare(report, pair.read_operating_export(export), [baseline], tmp_path)
    assert result["exclusion_counts"] == {"m0_revision_ledger_unverified": 1}


def test_revision_anchor_survives_repeated_receipts_and_distinguishes_reversion(tmp_path):
    archive(tmp_path)
    ordered(tmp_path, NOW + timedelta(minutes=9), changed=True)
    ordered(tmp_path, NOW + timedelta(minutes=10))
    root = tmp_path / "official"
    records = [json.loads(line) for line in (root / "receipts.jsonl").read_bytes().splitlines()]
    client = SimpleNamespace(root=root, records=records)
    anchors = []
    for minute in (7, 9, 10):
        stamp = NOW + timedelta(minutes=minute)
        receipt = next(r for r in records if r["finished_at"] == stamp.isoformat())
        anchors.append(revision_anchor(client, {"source": GAME}, receipt, stamp))
    assert anchors[0] == anchors[1]
    assert anchors[0]["received_at"] == (NOW + timedelta(minutes=7)).isoformat()
    assert anchors[2]["received_at"] == (NOW + timedelta(minutes=10)).isoformat()
