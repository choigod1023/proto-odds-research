import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'experiments' / 'accuracy-review-20261030'

def test_independent_ledger_and_detects_changed_source_result(tmp_path):
    shutil.copy(ROOT / 'recheck.py', tmp_path / 'recheck.py')
    shutil.copytree(ROOT / 'inputs', tmp_path / 'inputs')
    command = [sys.executable, '-X', 'utf8', str(tmp_path / 'recheck.py')]
    clean = subprocess.run(command, capture_output=True, text=True, encoding='utf8')
    assert clean.returncode == 0, clean.stderr
    result = json.loads((tmp_path / 'independent-check.json').read_text(encoding='utf8'))
    assert result['independent_settlements'] == 23752
    assert result['summary_values_checked'] == 1400
    target = tmp_path / 'inputs' / 'targets.jsonl.gz'
    with gzip.open(target, 'rt', encoding='utf8') as f:
        rows = [json.loads(line) for line in f]
    row = next(r for r in rows if r['event_id'] == 'kleague:2026:1:9')
    row['outcome'] = (row['outcome'] + 1) % len(row['sides'])
    with gzip.open(target, 'wt', encoding='utf8') as f:
        for item in rows:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    changed = subprocess.run(command, capture_output=True, text=True, encoding='utf8')
    assert changed.returncode != 0
    assert 'settlement' in changed.stdout
from datetime import datetime
import json
from pathlib import Path


def test_publication_gate_fixture_distinguishes_kickoff_and_decision():
    path = Path(__file__).resolve().parents[1] / 'experiments/accuracy-review-20261030/late-publication-fixture.json'
    f = json.loads(path.read_text(encoding='utf-8-sig'))
    published, decision, kickoff = (datetime.fromisoformat(f[k]) for k in ('source_reported_published_at', 'decision_at', 'kickoff'))
    assert published < kickoff  # prepare.py's publication check admits this timestamp
    assert published > decision  # unavailable even according to the source's own claim
