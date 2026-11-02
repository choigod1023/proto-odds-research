import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'experiments/terra-outcome-audit-20261031/recheck.py'
SOURCE=ROOT/'experiments/dynamic-score-state-model-20261030'

def test_recomputes_and_detects_altered_outcome(tmp_path):
    for folder in ('inputs','results'):
        (tmp_path/folder).mkdir()
    for file in ('inputs/targets.jsonl.gz','results/predictions.jsonl.gz','results/results.json'):
        shutil.copy2(SOURCE/file,tmp_path/file)
    out=tmp_path/'audit.json';cmd=[sys.executable,'-X','utf8',str(SCRIPT),str(tmp_path),str(out)]
    success=subprocess.run(cmd,capture_output=True,text=True,encoding='utf8')
    assert success.returncode==0,success.stderr
    obj=json.loads(out.read_text(encoding='utf8'))
    assert obj['actual_vote_2026_targets']==445 and obj['settlement_count_actual_vote_all_candidates_modes']==17800
    assert obj['summary_discrepancies']==0
    source=tmp_path/'inputs/targets.jsonl.gz'
    with gzip.open(source,'rt',encoding='utf8') as f:rows=[json.loads(line) for line in f]
    row=next(x for x in rows if x['actual_votes'] and x['kickoff'].startswith('2026'))
    row['outcome']=(row['outcome']+1)%len(row['sides'])
    with gzip.open(source,'wt',encoding='utf8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    assert subprocess.run(cmd,capture_output=True,text=True,encoding='utf8').returncode!=0
