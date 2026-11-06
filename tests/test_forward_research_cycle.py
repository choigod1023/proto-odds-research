from datetime import datetime, timezone
import gzip
import hashlib

from scripts import forward_research_cycle as cycle


def test_replay_uses_both_choices_at_the_same_quote():
    offer = {"game_no": "6", "winner": "원정", "market_favorite": "홈",
             "odds": {"홈": 1.6, "원정": 3.8}}
    row = {"frozen_pick": {"game_no": "6", "selection": "원정"}, "products": [offer]}
    void = {**row, "products": [{**offer, "winner": None}]}
    result = cycle.metrics([row, void])
    assert result["existing_pick"]["decided"] == 1
    assert result["existing_pick"]["hit_rate"] == 1
    assert result["existing_pick"]["roi"] == 1.4
    assert result["same_offer_market_favorite"]["roi"] == -0.5


def test_retained_bytes_require_matching_slot_hash(tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    root = tmp_path / "retained"
    body = b"<html>observed</html>"
    digest = hashlib.sha256(body).hexdigest()
    slot = cycle.snapshot.receipt_body_slot(slots, digest)
    slot.write_bytes(gzip.compress(b"unrelated round"))
    cycle.retain_round_bodies([{"body_sha256": digest}], slots, root)
    assert not list(root.iterdir())
    slot.write_bytes(gzip.compress(body))
    cycle.retain_round_bodies([{"body_sha256": digest}], slots, root)
    assert gzip.decompress((root / (digest + ".html.gz")).read_bytes()) == body


def test_archived_public_response_checks_bytes_and_clock(tmp_path):
    received = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    cycle.archive(tmp_path, "a", b'{"view":{"revision":"1"}}', received)
    [(body, clock)] = cycle.archived(tmp_path)
    assert clock == received
    assert body["view"]["revision"] == "1"
