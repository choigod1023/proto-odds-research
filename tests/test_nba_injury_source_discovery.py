from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from discover_nba_injury_sources import discover, source_clock

URL = "https://official.nba.com/nba-injury-report-2026-27-season/"
PDF = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-08_02_30AM.pdf"


def fixture(html=None):
    body = (html or f'<h1>NBA Injury Report: 2026-27 Season</h1><a href="{PDF}">report</a>').encode()
    return dict(url=URL, final_url=URL, status=200, body_sha256=hashlib.sha256(body).hexdigest(),
                received_at="2026-11-08T07:40:00+00:00", bytes=len(body)), body


def test_discovered_link_is_not_a_received_pdf_or_pregame_fact():
    meta, body = fixture()
    result = discover(meta, body, season="2026-27", target_date="2026-11-08")
    report = result["reports"][0]
    assert result["discovered"] == 1 and report["url"] == PDF
    assert report["published_at_claim"] == "2026-11-08T07:30:00+00:00"
    assert report["pdf_body_captured"] is False and report["training_eligible"] is False
    assert result["training_eligible"] is False


def test_actual_old_season_redirect_cannot_become_historical_evidence():
    meta, body = fixture()
    meta["url"] = "https://official.nba.com/nba-injury-report-2025-26-season/"
    with pytest.raises(ValueError, match="season_or_index_redirect_mismatch"):
        discover(meta, body, season="2025-26")


def test_hash_header_and_timezone_are_required():
    meta, body = fixture()
    with pytest.raises(ValueError, match="hash_mismatch"):
        discover(meta, body+b"changed", season="2026-27")
    meta["received_at"] = "2026-11-08T07:40:00"
    with pytest.raises(ValueError, match="timezone"):
        discover(meta, body, season="2026-27")
    meta, body = fixture('<h1>NBA Injury Report: 2025-26 Season</h1>')
    with pytest.raises(ValueError, match="content_season_mismatch"):
        discover(meta, body, season="2026-27")


@pytest.mark.parametrize("stamp", ["2026-11-01_01_30AM", "2026-03-08_02_30AM"])
def test_dst_ambiguous_or_nonexistent_source_claim_is_rejected(stamp):
    with pytest.raises(ValueError, match="ambiguous_or_nonexistent"):
        source_clock(f"https://ak-static.cms.nba.com/referee/injury/Injury-Report_{stamp}.pdf")


def test_summer_offset_is_not_fixed_est():
    clock = source_clock("https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-07-08_02_30PM.pdf")
    assert clock == datetime(2026, 7, 8, 18, 30, tzinfo=timezone.utc)


def test_date_filter_dedup_limit_and_future_links():
    other = PDF.replace("02_30AM", "02_15AM")
    future = PDF.replace("02_30AM", "11_30PM")
    wrong_day = PDF.replace("2026-11-08", "2026-11-07")
    bad_host = PDF.replace("ak-static.cms.nba.com", "bad.example")
    meta, body = fixture('<h1>NBA Injury Report: 2026-27 Season</h1>'+''.join(
        f'<a href="{url}">report</a>' for url in [PDF, PDF, other, future, wrong_day, bad_host]))
    result = discover(meta, body, season="2026-27", target_date="2026-11-08", max_reports=1)
    assert result["discovered"] == 2 and result["returned"] == 1
    assert result["reports"][0]["url"] == PDF
    assert set(result["rejected"]) == {"source_clock_after_index_receipt", "not_official_injury_pdf"}
    with pytest.raises(ValueError, match="report_limit"):
        discover(meta, body, season="2026-27", max_reports=65)


def test_no_requested_historical_date_is_explicit_not_fabricated():
    meta, body = fixture()
    result = discover(meta, body, season="2026-27", target_date="2026-10-15")
    assert result["status"] == "no_matching_report_links" and result["reports"] == []
