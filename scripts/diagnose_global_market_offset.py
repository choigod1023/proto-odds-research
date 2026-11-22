"""Posthoc simpler null control, with no new parameter search or model choice."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path


def diagnose(rows, selected):
    history = [r for r in rows if r['split'] == 'meta_history_2024']
    evaluation = [r for r in rows if r['split'] == 'evaluation_2025']
    if len(history) != 228 or len(evaluation) != 227 or not selected:
        raise ValueError('unexpected frozen study inputs')
    k, prior = selected[2:]
    multiplier = k / (k + prior)  # Reuse the frozen tuning values; do not search.
    residual = [sum((r['y'] == j)-r['market_p'][j] for r in history)/len(history) for j in range(3)]
    predictions = []
    hits, profit, classes, changes, loss = 0, Decimal(0), Counter(), 0, 0.0
    for r in evaluation:
        p = [max(1e-6,r['market_p'][j]+multiplier*residual[j]) for j in range(3)]
        total = sum(p); p = [v/total for v in p]
        pick = max(range(3), key=lambda j:(p[j],-j))
        hit = pick == r['y']
        net = Decimal(str(r['prices'][pick]))-1 if hit else Decimal(-1)
        predictions.append({'id':r['id'],'probability':p,'pick':pick})
        hits += hit; profit += net; classes[pick] += 1
        changes += pick != max(range(3), key=lambda j:(r['market_p'][j],-j))
        loss -= math.log(p[r['y']])
    return {'schema_version':'global-market-offset-posthoc-v1',
        'scope':'posthoc diagnostic null; no retuning/promotion; 2025 already inspected',
        'method':'market + frozen k/(k+prior) * global 2024 mean outcome-minus-market residual; no neighbors',
        'history_n':len(history),'evaluation_n':len(evaluation),'fixed_multiplier':multiplier,
        'global_residual':residual,'hits':hits,'accuracy':hits/len(evaluation),
        'net_units':str(profit),'roi':float(profit/Decimal(len(evaluation))),
        'logloss':loss/len(evaluation),'classes':dict(classes),'changes_vs_market':changes,
        'predictions':predictions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'experiments/neighbor-tuning-20261102'
    source = root/'inputs/oof.jsonl'; tuning = root/'results.json'
    rows = [json.loads(s) for s in source.read_text(encoding='utf8').splitlines()]
    report = diagnose(rows,json.loads(tuning.read_text(encoding='utf8'))['selected'])
    report['input_lf_sha256'] = hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    with args.output.open('x',encoding='utf8',newline='\n') as f:
        json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:report[k] for k in ('hits','accuracy','roi','scope')}))


if __name__ == '__main__':
    main()
