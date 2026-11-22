from copy import deepcopy
import json
from pathlib import Path

from scripts.diagnose_global_market_offset import diagnose


def test_evaluation_labels_do_not_change_global_offset_predictions():
    path=Path(__file__).resolve().parents[1]/'experiments/neighbor-tuning-20261102'
    rows=[json.loads(s) for s in (path/'inputs/oof.jsonl').read_text(encoding='utf8').splitlines()]
    selected=json.loads((path/'results.json').read_text(encoding='utf8'))['selected']
    before=diagnose(rows,selected)
    assert before['hits']==105 and before['net_units']=='-18.45'
    changed=deepcopy(rows)
    for row in changed:
        if row['split']=='evaluation_2025':row['y']=(row['y']+1)%3
    assert diagnose(changed,selected)['predictions']==before['predictions']
