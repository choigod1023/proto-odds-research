"""Reproduce response-parent memory comparison with synthetic repository data.

Local only; never reads a production database. Writes a unique temporary fixture
inside this checkout's ignored .local-test directory, then deletes only that fixture.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = ROOT / 'docs/data/picks_v2.json'
data = json.loads(source.read_text(encoding='utf8'))
original = [(section, game) for section in ('live', 'past') for game in data.get(section, [])]
if not original:
    raise ValueError('fixture has no games')
data['live'], data['past'] = [], []
for i in range(2969):
    section, game = original[i % len(original)]
    row = copy.deepcopy(game)
    row['home'] = str(row.get('home', '')) + ' [fixture-' + str(i // len(original)) + ']'
    data[section].append(row)
workspace = ROOT / '.local-test'
workspace.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='volleyball-memory-profile-', dir=workspace) as directory:
    fixture = Path(directory) / 'picks.json'
    fixture.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    print(json.dumps({'synthetic_rows': 2969, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'fixture_bytes': fixture.stat().st_size, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()}), flush=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/measure_match_response_memory.py'),
                    '--fixture', str(fixture)], cwd=ROOT, check=True)
