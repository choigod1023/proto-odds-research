"""Independent settlement and saved-parameter replay, without importing evaluator."""
from pathlib import Path
from decimal import Decimal,ROUND_DOWN
from datetime import datetime
import argparse
import hashlib
import json
import math

from collect_baseball_pregame_evidence import read


def verify(folder):
    report=json.loads((folder/'mlb-comparison.json').read_text(encoding='utf8'))
    rows=read(folder/'mlb-evaluation.jsonl');source=read(folder/'mlb-snapshots.jsonl')
    assert len(rows)==len({r['gamePk'] for r in rows})==report['evaluation_n']==116
    for r in source:
        if r['accepted']:
            assert datetime.fromisoformat(r['snapshot_at'])<=datetime.fromisoformat(r['cutoff_at'])<datetime.fromisoformat(r['start_at'])
            for side in ['home','away']:
                s=r['sides'][side]
                assert s['starter']['id'] and len({p['id'] for p in s['lineup']})==9
    chosen=min(report['candidates'],key=lambda n:(-report['candidates'][n]['validation']['accuracy'],report['candidates'][n]['validation']['brier'],n))
    assert chosen==report['selected']
    checked=0
    for name in list(report['candidates'])+list(report['policies']):
        candidate=name in report['candidates'];m=report['candidates'][name]['evaluation'] if candidate else report['policies'][name]
        wins=pushes=net=0
        for row in rows:
            p=row['candidate_probabilities'][name] if candidate else row['probabilities'][name]
            if candidate:
                config=report['models'][name];model=config['before2025' if row['start_at'][:4]=='2025' else 'before2026']
                z=model['beta'][0]
                for i,v in enumerate(row['features'][:config['size']]):z+=model['beta'][i+1]*max(-6,min(6,(v-model['center'][i])/model['scale'][i]))
                assert abs(1/(1+math.exp(-z))-p)<1e-12
            t=row['target'];side='home' if p>=.5 else 'away'
            if t['hg']==t['ag']:pushes+=1;continue
            winner='home' if t['hg']>t['ag'] else 'away'
            if side==winner:
                wins+=1;net+=int((Decimal(str(t['prices'][t['order'].index(side)]))*10000).to_integral_value(rounding=ROUND_DOWN))-10000
            else:net-=10000
        assert (wins,pushes,net)==(m['wins'],m['pushes'],m['net_krw'])
        assert abs(net/(10000*len(rows))-m['roi'])<1e-12
        checked+=1
    manifest={f.name:hashlib.sha256(f.read_bytes().replace(b'\r\n',b'\n') if f.suffix!='.gz' else f.read_bytes()).hexdigest()
        for f in sorted(folder.iterdir()) if f.suffix in ['.json','.jsonl','.gz'] and f.name!='verified-sha256.json'}
    (folder/'verified-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(dict(ok=True,fixtures=len(rows),independent_policies=checked,probability_replays=116*len(report['candidates']),snapshot_rows=len(source))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);verify(p.parse_args().folder)
