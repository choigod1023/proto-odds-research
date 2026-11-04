from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from scripts.collect_tipster_comparison_sources import capture, link


class Response:
    status_code = 200
    headers = {}
    encoding = "utf-8"
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, *args): yield "원문 내용".encode()


class Session:
    calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        assert kwargs["allow_redirects"] is False
        return Response()


def test_complete_article_bytes_are_saved_and_cached_receipt_is_not_rewritten(tmp_path):
    sess = Session(); budget = dict(used=0, limit=2)
    url = "https://pick79.com/posts/detail/example/"
    first, text = capture(sess, url, tmp_path, "capture-one", budget)
    second, again = capture(sess, url, tmp_path, "capture-one", budget)
    assert sess.calls == 1 and first == second and text == again
    raw = gzip.decompress((tmp_path / first["body_file"]).read_bytes())
    assert raw.decode() == "원문 내용" and first["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert first["timestamp_basis"] == "http_response_body_completed"
    capture(sess, url, tmp_path, "capture-two", budget)
    assert sess.calls == 2 and len(list(tmp_path.glob("*.body.gz"))) == 2


def test_corrupt_article_cache_cannot_be_reparsed_as_original(tmp_path):
    sess = Session(); budget = dict(used=0, limit=1)
    url = "https://pick79.com/posts/detail/example/"
    meta, _ = capture(sess, url, tmp_path, "once", budget)
    (tmp_path / meta["body_file"]).write_bytes(gzip.compress(b"different"))
    with pytest.raises(ValueError, match="cached_article_bytes_changed"):
        capture(sess, url, tmp_path, "once", budget)


def inputs():
    article = dict(sport="bs", league="KBO", home="삼성", away="KT", selected="home",
                   kickoff="2026-11-04T18:30:00+09:00", author="writer", observed_at="2026-11-04T05:00:00Z",
                   source_url="https://pick79.com/posts/detail/example", content_hash="a"*64, selection_text="삼성 승리")
    meta = dict(url=article["source_url"], received_at=article["observed_at"], timestamp_basis="http_response_body_completed", body_sha256="b"*64)
    price = dict(record_id="price", kind="price", sport="bs", league="KBO", home="삼성", away="KT",
                 kickoff="2026-11-04T09:30:00Z", payload=dict(product_id="G101:260130:3", market="winner_2way", period="full", line=None, settlement_rule="draw_void_refund", odds={"home":1.8,"away":2.0}))
    return article, meta, price


def test_exact_fixture_mapping_uses_same_product_and_handles_equivalent_timezones():
    article, meta, price = inputs()
    row = link(article, meta, [price])
    assert row["payload"]["product_id"] == price["payload"]["product_id"]
    assert row["payload"]["selection"] == "home" and row["body_sha256"] == meta["body_sha256"]


def test_old_published_claim_is_not_replacement_for_late_receipt():
    article, meta, price = inputs()
    article.update(observed_at="2026-11-04T10:00:00Z", published_at="2026-11-04T01:00:00Z")
    with pytest.raises(ValueError, match="outside_pregame_window"):
        link(article, meta, [price])


def test_two_receipts_of_same_article_append_observations_without_rewriting_first():
    article, meta, price = inputs()
    first = link(article, meta, [price])
    meta["received_at"] = article["observed_at"] = "2026-11-04T05:10:00Z"
    second = link(article, meta, [price])
    assert first["record_id"] != second["record_id"]
    assert first["payload"]["source_pick_id"] == second["payload"]["source_pick_id"]


def test_unknown_settlement_or_multiple_products_are_not_guessed():
    article, meta, price = inputs()
    other = deepcopy(price); other["payload"]["product_id"] = "other"
    with pytest.raises(ValueError, match="ambiguous_proto_product"):
        link(article, meta, [price, other])
    price["payload"]["settlement_rule"] = "unknown"
    with pytest.raises(ValueError, match="unmatched_or_ambiguous_fixture"):
        link(article, meta, [price])
