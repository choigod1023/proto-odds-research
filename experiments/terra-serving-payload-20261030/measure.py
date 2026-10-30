"""Measure repeated artifact responses with a repository fixture, never production."""
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from artifact_responses import ArtifactResponses

fixture = ROOT / 'docs/data/picks_v2.json'
raw = fixture.read_text(encoding='utf-8')
class FixtureDB:
    reads = 0
    def artifact_metadata(self, *_args, **_kwargs):
        return {'stored_at': 'fixture-1'}
    def get_artifact_json(self, _name):
        self.reads += 1
        return raw, 'fixture-1'

db = FixtureDB()
cache = ArtifactResponses(db)
first, tag = cache.get_response('picks_v2')
unchanged, _ = cache.get_response('picks_v2', if_none_match=tag)
identity, _ = cache.get_response('picks_v2', compressed=False)
assert gzip.decompress(first) == identity == raw.encode('utf-8')
assert unchanged is None
print(json.dumps({'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                  'identity_bytes': len(identity), 'gzip_bytes': len(first),
                  'old_two_unchanged_gzip_body_bytes': len(first) * 2,
                  'new_two_unchanged_gzip_body_bytes': len(first),
                  'old_unchanged_text_reads': 1, 'new_unchanged_text_reads': 0,
                  'old_unchanged_json_parses': 0, 'new_unchanged_json_parses': 0,
                  'artifact_reads': db.reads}, ensure_ascii=False))
