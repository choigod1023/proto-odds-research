"""Synthetic pages only; real third-party bodies remain in external local cache."""
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rd_tipster_source_links as m

URL = "https://www.goli.tv/board/analysis/123"
OBS = "2026-10-30T05:00:00Z"


def html(title="LG 대 키움 KBO 09월08일 맞대결", body="예상 승리팀 : LG 트윈스", author="가상작성자", pub="2026-09-08 02:09"):
    return (f'<h1 id="page-heading">{title}</h1><div class="writer_box"><b class="_userView">{author}</b></div>'
            f'<div class="view_stat">{pub}</div><div id="partner_content">{body}</div>').encode()


def case(**kwargs):
    row = dict(event_id="event-1", league="KBO", home="LG 트윈스", away="키움 히어로즈",
               kickoff="2026-09-08T18:30:00+09:00", cohort="retrospective_test",
               sides=["home", "away"], external_p=[1.0, 0.0], decision_at="2026-09-08T18:00:00+09:00")
    return dict(row, **kwargs)


def vote(**kwargs):
    row = m.join_article(m.parse_article(html(), URL, OBS), [case()])
    return dict(row, **kwargs)


def test_realistic_baseball_explicit_winner_and_time_claim():
    v = vote()
    assert v["selection"] == "home" and v["market"] == "full_game_moneyline"
    assert v["published_at_claim"] == "2026-09-08T02:09:00+09:00"
    assert v["observed_at"] == OBS
    assert v["availability_tier"] == "retrospective_claimed_pregame"
    assert v["join_evidence"]["source_kickoff"] is None
    assert v["document_sha256"] == m.sha(html())


def test_soccer_first_half_pick_is_not_full_game_vote():
    raw = html("인천 대 대전 K리그1 09월20일 맞대결",
               "베팅 요약 및 추천<p>[승/무/패] 인천 승</p>전반전<p>[승/무/패] 무승부</p>", pub="2026-09-20 01:00")
    a = m.parse_article(raw, URL, OBS)
    assert a["selection"] == "home" and a["market"] == "승무패"


def test_explicit_soccer_loss_in_summary():
    a = m.parse_article(html("성남 대 화성 K리그2 09월20일 맞대결",
        "베팅 요약 및 추천<p>[승/무/패] 성남 패</p>", pub="2026-09-20 01:00"), URL, OBS)
    assert a["selection"] == "away"


@pytest.mark.parametrize("body,reason", [
    ("LG 승리 가능성이 높습니다", "missing_explicit_full_game_winner"),
    ("승1패 : LG 승리<p>핸디 : LG 승리</p><p>언더 오버 : 오버</p>", "missing_explicit_full_game_winner"),
    ("예상 승리팀 : LG<p>예상 승리팀 : 키움</p>", "ambiguous_or_unsupported_selection"),
    ("예상 승리팀 : 알수없는팀", "unknown_explicit_selection_team"),
])
def test_no_sentiment_or_ancillary_pick_inference(body, reason):
    with pytest.raises(ValueError, match=reason):
        m.parse_article(html(body=body), URL, OBS)


@pytest.mark.parametrize("kwargs,reason", [
    ({"author": ""}, "missing_author"),
    ({"pub": "09-08 02:09"}, "missing_publication_year_and_time"),
    ({"pub": "2025-09-01 02:09"}, "publication_fixture_date_distance"),
])
def test_missing_identity_and_publication_are_not_invented(kwargs, reason):
    with pytest.raises(ValueError, match=reason):
        m.parse_article(html(**kwargs), URL, OBS)


def test_npb_explicit_alias_and_english_frozen_names():
    a = m.parse_article(html("라쿠텐 대 니혼햄 NPB 09월05일 맞대결",
        "예상 승리팀 : 홋카이도 닛폰햄 파이터스", pub="2026-09-05 02:00"), URL, OBS)
    c = case(league="NPB", home="Tohoku Rakuten Golden Eagles", away="Hokkaido Nippon-Ham Fighters",
             kickoff="2026-09-05T18:00:00+09:00")
    assert m.join_article(a, [c])["selection"] == "away"
    assert m.team("롯데", "NPB") != m.team("지바롯데", "NPB")


def test_ambiguous_doubleheader_is_not_forced_to_one_event():
    a = m.parse_article(html(), URL, OBS)
    with pytest.raises(ValueError, match="ambiguous_fixture"):
        m.join_article(a, [case(), case(event_id="event-2", kickoff="2026-09-08T14:00:00+09:00")])


def test_exact_source_time_disambiguates_but_never_infers_time():
    a = dict(m.parse_article(html(), URL, OBS), source_kickoff="2026-09-08T18:30:00+09:00")
    assert m.join_article(a, [case(), case(event_id="event-2", kickoff="2026-09-08T14:00:00+09:00")])["event_id"] == "event-1"
    with pytest.raises(ValueError, match="no_exact_primary_fixture"):
        m.join_article(a, [case(kickoff="2026-09-08T18:35:00+09:00")])


def test_source_home_order_does_not_override_frozen_home_order():
    a = m.parse_article(html(), URL, OBS)
    assert m.join_article(a, [case(home="키움", away="LG")])["selection"] == "away"


def test_year_is_used_not_only_month_day():
    a = m.parse_article(html(), URL, OBS)
    with pytest.raises(ValueError, match="no_exact_primary_fixture"):
        m.join_article(a, [case(kickoff="2025-09-08T18:30:00+09:00")])


def test_publication_claim_does_not_become_actual_observation():
    assert m.availability("2026-09-08T02:00:00+09:00", OBS, case()["kickoff"]) == "retrospective_claimed_pregame"
    assert m.availability(None, "2026-09-08T17:59:00+09:00", case()["kickoff"]) == "captured_pregame"
    assert m.availability(None, OBS, case()["kickoff"]) == "diagnostic_no_pregame_publication_claim"
    assert m.availability("2026-11-01T00:00:00+09:00", OBS, case()["kickoff"]) == "diagnostic_future_publication_claim"
    with pytest.raises(ValueError, match="timezone_required"):
        m.stamp("2026-09-08T02:00:00")


def test_identical_documents_across_sites_count_once():
    first = vote()
    second = dict(first, source_site="other.example", author="다른이름", source_url="https://other.example/a")
    kept, audit = m.deduplicate([first, second])
    assert len(kept) == 1 and audit[1]["decision"] == "identical_document_hash"


def test_copied_body_different_page_hash_counts_once():
    first = vote()
    second = dict(first, source_site="other.example", document_sha256="b"*64)
    kept, audit = m.deduplicate([first, second])
    assert len(kept) == 1 and audit[1]["decision"] == "identical_article_body_hash"


def test_same_display_name_other_site_is_not_identity_equivalence():
    first = vote()
    second = dict(first, source_site="other.example", document_sha256="b"*64, article_body_sha256="c"*64)
    assert len(m.deduplicate([first, second])[0]) == 2  # candidates, NOT certified independent humans


def test_same_site_author_conflicts_are_excluded():
    first = vote()
    second = dict(first, selection="away", document_sha256="b"*64)
    kept, audit = m.deduplicate([first, second])
    assert not kept and {r["decision"] for r in audit} == {"conflicting_site_author"}


def test_missing_hash_not_a_shared_document_identity():
    first = vote(document_sha256=None, article_body_sha256=None)
    second = dict(first, author="다른이름")
    assert len(m.deduplicate([first, second])[0]) == 2


def test_old_vector_disagreement_is_audited_not_overwritten():
    rows = [vote(), vote(author="다른작성자", selection="away", document_sha256="b"*64, article_body_sha256="c"*64)]
    result = m.audit_vectors(rows, [case()])[0]
    assert result["frozen_external_p"] == [1.0, 0.0]
    assert result["reconstructed_vote_share"] == [0.5, 0.5]
    assert not result["eligible_for_union"]


def test_cache_budget_persists_and_blocks_before_network(tmp_path):
    cache = m.PublicCache(tmp_path, limit=1)
    cache.log.write_text('{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="request_budget_exhausted"):
        cache.fetch(URL)


def test_forward_http_adapter_caches_actual_capture_without_second_request(tmp_path, monkeypatch):
    monkeypatch.delenv("WBC_PROXY_URL", raising=False)
    calls = []
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        is_redirect = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def iter_content(self, size):
            yield html()
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 12)
            calls.append(url)
            return Response()
    monkeypatch.setattr(m.requests, "Session", Session)
    cache = m.PublicCache(tmp_path, limit=1)
    first, raw = cache.fetch(URL)
    second, again = cache.fetch(URL)
    assert first == second and raw == again == html() and calls == [URL]
    assert first["document_sha256"] == m.sha(html())
    assert m.stamp(first["fetch_started_at"]) <= m.stamp(first["observed_at"])


@pytest.mark.parametrize("url", ["http://www.goli.tv/", "https://user:secret@www.goli.tv/", "https://localhost/", "https://www.goli.tv:123/"])
def test_no_credentials_or_unapproved_hosts(tmp_path, url):
    with pytest.raises(ValueError, match="unsupported_public_url"):
        m.PublicCache(tmp_path).fetch(url)


def test_old_export_preserves_original_metadata_and_nulls(tmp_path):
    path = tmp_path / "old.gz"
    frame = dict(case(), raw_forecasts=[dict(author="원작성자", source_url=URL, side="home",
                 published_at_claim=None, observed_at=OBS, source_sha256=None)])
    m.write_jsonl(path, [frame])
    rows, rejected = m.normalize_old(path, [case()])
    assert not rejected and len(rows) == 1
    assert rows[0]["author"] == "원작성자" and rows[0]["document_sha256"] is None
    assert rows[0]["published_at_claim"] is None and rows[0]["market"] == "full_game_moneyline"
    assert set(rows[0]["metadata_missing_reasons"]) == {"original_document_hash_missing", "original_publication_claim_missing"}


def test_real_artifact_contract_and_vector_gate():
    out = m.OUT
    if not (out / "manifest.json").exists():
        pytest.skip("collected artifacts not built")
    def read(name):
        return [json.loads(line) for line in gzip.open(out/name, "rt", encoding="utf-8")]
    cases = m.read_cases(m.ROOT / "experiments/pregame-feature-rd-20261030/inputs/cases.jsonl.gz")
    claimed = {r["event_id"] for r in cases if r["cohort"].startswith("retrospective_")}
    required = {"event_id", "league", "kickoff", "selection", "market", "author", "source_site",
                "source_url", "published_at_claim", "observed_at", "document_sha256", "join_evidence", "availability_tier"}
    for name in ("old-votes-normalized.jsonl.gz", "new-votes.jsonl.gz"):
        for row in read(name):
            assert required <= row.keys()
            assert row["event_id"] in claimed
            assert row["selection"] in {"home", "draw", "away"}
            assert row["market"] in {"full_game_moneyline", "승무패"}
    vectors = read("old-vector-audit.jsonl.gz")
    assert len(vectors) == 538 and sum(not r["eligible_for_union"] for r in vectors) == 4
    old = read("old-votes-normalized.jsonl.gz")
    assert m.audit_vectors(old, cases) == vectors
    assert len(read("new-votes.jsonl.gz")) == 11
    assert all(r["document_hash_kind"] == "article_dom_utf8" for r in read("new-votes.jsonl.gz"))
