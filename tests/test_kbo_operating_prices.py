"""Synthetic HTTP receipts. No historical capture is created."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research.kbo_forward import test_runner as fixtures
from research.kbo_forward.server_cycle import publish_index
from research.m1_service import observer
from src import kbo_operating_prices as prices, m0_forward_provider as provider
from src.wisetoto import parse_rows
from tests.test_multi_market_recommendations import build, game, score_distribution, NOW


def page(date, home, away, *, status="경기전"):
    return f"""<div class="gameinfo"><ul>
    <li class="a1">31</li><li class="a2">{date}</li><li class="a3 bs"></li>
    <li class="a4">KBO</li><li class="un">U 8.5</li>
    <li class="a6_un">{home}</li><li class="a8_un">{away}</li>
    <li class="a9">1.80</li><li class="a9">-</li><li class="a9">2.00</li>
    <li>{status}</li></ul></div>"""


def observe(root, date, home, away, at, *, number=131):
    html = page(date, home, away)
    prices.observe(root, 2026, number, parse_rows(html, 2026, number), at, html.encode())


def test_replays_raw_price_and_only_attaches_comparison_to_matching_web_offer(tmp_path):
    g = game()
    event = {"season": 2026, "home_name": "기아", "away_name": "SSG",
             "kickoff_at": "2026-11-06T19:00:00+09:00"}
    live = tmp_path / "live"
    archive = tmp_path / "archive"
    observe(live, g["date"], "KIA타이거즈", "SSG랜더스", NOW)
    evidence = prices.capture(live, archive, event, NOW)
    assert evidence["products"] == {
        "31": {"market": "언더오버", "line": 8.5, "rule": "total_half", "odds": {"언더": 1.8, "오버": 2.0}}}
    prices.verify(archive, evidence, event, NOW)
    model = dict(score_distribution(), operating_price_evidence=evidence)

    def market_items(match):
        return {i["game_no"]: i for i in build(
            match, [], NOW.isoformat(), NOW.isoformat(), distribution=model)["items"]}

    original = market_items(g)
    assert "m0_actual" in original["31"]["comparisons"]
    assert "m0_actual" not in original["32"]["comparisons"]
    changed = deepcopy(g)
    changed["options"][0]["배당"] = 1.81
    actual = market_items(changed)
    assert "m0_actual" not in actual["31"]["comparisons"]
    assert actual["31"]["model"] == original["31"]["model"]


def test_skips_late_stale_wrong_fixture_and_ambiguous_price_receipts(tmp_path):
    g = game()
    event = {"season": 2026, "home_name": "KIA", "away_name": "SSG",
             "kickoff_at": "2026-11-06T19:00:00+09:00"}
    live = tmp_path / "live"
    archive = tmp_path / "archive"
    observe(live, g["date"], "KIA", "SSG", NOW)
    assert prices.capture(live, archive, dict(event, away_name="NC"), NOW) is None
    assert prices.capture(live, archive, event, NOW + timedelta(minutes=11)) is None
    assert prices.capture(live, archive, event, NOW - timedelta(seconds=1)) is None
    near_cutoff = NOW + timedelta(minutes=145)
    observe(live, g["date"], "KIA", "SSG", near_cutoff)
    assert prices.capture(live, archive, event, near_cutoff + timedelta(minutes=5)) is None
    observe(live, g["date"], "KIA", "SSG", near_cutoff, number=132)
    assert prices.capture(live, archive, event, near_cutoff) is None
    assert not archive.exists()


def test_server_cycle_freezes_verified_price_context_without_betman_and_replays_index(tmp_path, monkeypatch):
    payload = fixtures.payload("full_game_2way")
    payload["event"].update(home_id="SS", away_id="HT")
    payload["row"].update(home_id="SS", away_id="HT")
    official = fixtures.Official(payload)
    official.game["source"].update(HOME_NM="삼성", AWAY_NM="KIA")
    event = fixtures.runner.event(official.game)
    start = fixtures.ledger.moment(event["kickoff_at"]).astimezone(prices.KST)
    now = start - timedelta(hours=2)
    live = tmp_path / "kbo-operating-price-v1"
    state = tmp_path / "kbo-m0-forward-v1"
    actual = tmp_path / "kbo-m0-actual-forward-v1"

    def forbid_betman(*args):
        raise AssertionError("operating price route must not call Betman")

    def snapshot(row):
        result = original_snapshot(row)
        result["payload"]["event"] = event
        return result

    original_snapshot = official.snapshot
    official.snapshot = snapshot
    monkeypatch.setattr(observer, "_verify_archives", lambda *args: None)
    monkeypatch.setattr(provider, "_verify_kbo_league", lambda *args: None)
    options = dict(official=official, clock=lambda: now, infer=fixtures.old.fake_infer,
                   operating_prices=live, m1_state=actual, price_fn=forbid_betman)
    report = fixtures.runner.run_once(state, **options)
    assert report["unpriced_this_run"] == 1 and not (actual / "predictions.jsonl").exists()
    observe(live, start.strftime("%m.%d(금) %H:%M"), "삼성", "KIA", now)
    report = fixtures.runner.run_once(state, **options)
    assert report["captured_this_run"] == 0 and report["m1_status"] == "recorded"
    record, = observer.read(actual / "predictions.jsonl")
    assert not record["roi_eligible"] and record["input"]["operating_price_evidence"]["round"] == 131
    assert publish_index(actual) == 1
    request = dict(league="KBO", home_team_id="SS", away_team_id="HT", kickoff_at=event["kickoff_at"])
    result = provider.get_distribution(request, now, now, root=tmp_path)
    assert result["status"] == "available"
    assert result["operating_price_evidence"] == record["input"]["operating_price_evidence"]
    digest = result["operating_price_evidence"]["packet_sha256"]
    (state / "operating-price-evidence" / (digest + ".gz")).write_bytes(b"tampered")
    provider._cache.clear()
    assert provider.get_distribution(request, now, now, root=tmp_path)["status"] == "unavailable"
def test_live_collector_retains_its_response_and_replays_handicap_team_names(tmp_path, monkeypatch):
    import odds_live

    g = game()
    html = page(g["date"], "KIA", "SSG")
    handicap = (html.replace('class="a1">31', 'class="a1">32')
                .replace('class="un">U 8.5', 'class="hm">H -1.5')
                .replace('class="a6_un">KIA', 'class="a6">KIA 2.5')
                .replace('class="a8_un">SSG', 'class="a8">1 SSG'))
    html += handicap
    body = html.encode()
    rows = parse_rows(html, 2026, 131)
    monkeypatch.setattr(odds_live, "_start_hint", lambda year: 131)
    monkeypatch.setattr(odds_live, "_recent_scan_rounds", lambda previous: [131])
    monkeypatch.setattr(odds_live, "_fetch_observation",
                        lambda session, year, number: (rows, NOW, body) if number == 131 else None)
    result = odds_live.collect(kbo_prices=tmp_path / "live")
    assert result["odds"]["131"] == {"31": [1.8, 2.0], "32": [1.8, 2.0]}
    event = {"season": 2026, "home_name": "KIA", "away_name": "SSG",
             "kickoff_at": "2026-11-06T19:00:00+09:00"}
    evidence = prices.capture(tmp_path / "live", tmp_path / "archive", event, NOW)
    assert evidence["products"]["32"] == {
        "market": "핸디캡", "line": -1.5, "rule": "handicap_half",
        "odds": {"핸디홈": 1.8, "핸디원정": 2.0}}
