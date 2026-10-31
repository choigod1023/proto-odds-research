"""Independent saved forecast settlement check against original target prices."""
import collections
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
with gzip.open(ROOT/'inputs/targets.jsonl.gz','rt',encoding='utf-8') as f:
    rows=[json.loads(line) for line in f]
targets={r['event_id']:r for r in rows if r['actual_votes']}
forecasts=[json.loads(x) for x in (ROOT/'forecasts.jsonl').read_text(encoding='utf-8').splitlines()]
settled=[json.loads(x) for x in (ROOT/'settlements.jsonl').read_text(encoding='utf-8').splitlines()]
assert len(forecasts)==len(settled)
checks=collections.Counter(); totals=collections.defaultdict(lambda: [0,Decimal(0)])
for forecast, saved in zip(forecasts,settled):
    assert 'outcome' not in forecast and 'profit' not in forecast
    target=targets[forecast['event_id']]; y=target['outcome']; pick=forecast['pick']
    expected=Decimal(str(target['odds'][pick]))-Decimal(1) if pick==y else Decimal(-1)
    assert saved['event_id']==forecast['event_id'] and saved['outcome']==y and saved['model']==forecast['model'] and saved['mode']==forecast['mode']
    assert Decimal(saved['profit'])==expected
    key=(forecast['league'],forecast['model'],forecast['mode']); totals[key][0]+=int(pick==y);totals[key][1]+=expected; checks['forecast_settlements']+=1
for row in json.loads((ROOT/'results.json').read_text(encoding='utf-8'))['metrics']:
    if row['league']=='ALL': continue
    hits,pnl=totals[(row['league'],row['model'],row['mode'])]
    assert hits==row['hits'] and abs(float(pnl/Decimal(row['n']))-row['roi'])<1e-12
checks['league_cells']=sum(r['league']!='ALL' for r in json.loads((ROOT/'results.json').read_text(encoding='utf-8'))['metrics'])
checks['hashes']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('protocol.json','negative-control.json','results.json','forecasts.jsonl','settlements.jsonl')}
(ROOT/'verification.json').write_text(json.dumps(dict(checks),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(checks),ensure_ascii=False))
