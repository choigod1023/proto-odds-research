import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_expanded_proto_archive_manifest import DETAIL_URL, build, verified_body


class ExpandedArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def capture(self, round_id, name, marker=0):
        body = {"gmTs": round_id, "currentLottery": {"gmTs": round_id},
                "compSchedules": {"keys": ["result"], "datas": [[marker]]}}
        raw = json.dumps(body).encode()
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(raw))
        return {"round": round_id, "body_file": name,
                "body_sha256": hashlib.sha256(raw).hexdigest()}

    def base(self, sources):
        path = self.root / "base.json"
        path.write_text(json.dumps({"source_roots": [str(self.root)],
                                   "sources": [{**s, "source_root_index": 0} for s in sources]}))
        return path

    def test_preserves_base_snapshot_and_earliest_new_round_not_result(self):
        base_capture = self.capture("260001", "old.gz", 1)
        changed = self.capture("260001", "changed.gz", 999)
        later = self.capture("250001", "later.gz", 999)
        early = self.capture("250001", "early.gz", -999)
        attempts = []
        for source, received in [(changed, "2026-11-01"), (later, "2026-11-03"), (early, "2026-11-02")]:
            attempts.append({**source, "url": DETAIL_URL, "http_status": 200,
                             "request_json": {"gmId": "G101", "gmTs": source["round"]},
                             "received_at": received})
        ap = self.root / "attempts.json"
        ap.write_text(json.dumps(attempts))
        result = build(self.base([base_capture]), [ap])
        self.assertEqual([s["body_file"] for s in result["sources"]], ["old.gz", "early.gz"])
        self.assertEqual(len(result["excluded_round_variants"]), 2)
        self.assertFalse(result["execution_price_verified"])

    def test_duplicate_does_not_multiply_sample_and_hashes_are_checked(self):
        source = self.capture("260001", "one.gz")
        result = build(self.base([source, source]), [])
        self.assertEqual(result["source_round_count"], 1)
        self.assertEqual(result["duplicate_captures"], 1)
        (self.root / "one.gz").write_bytes(gzip.compress(b'{}'))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            build(self.base([source]), [])

    def test_rejects_wrong_round_even_with_matching_hash(self):
        source = self.capture("260001", "one.gz")
        with self.assertRaisesRegex(ValueError, "round mismatch"):
            verified_body(self.root, source["body_file"], source["body_sha256"], "260002")

    def test_rejects_archive_path_escape(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            verified_body(self.root, "../other.gz", "unused", "260001")

    def test_unsupported_year_does_not_enter_eval_manifest(self):
        source = self.capture("240001", "old-training.gz")
        result = build(self.base([source]), [])
        self.assertEqual(result["source_round_count"], 0)


if __name__ == "__main__":
    unittest.main()
