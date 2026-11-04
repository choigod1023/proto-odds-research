"""Inspect a preserved Betman response offline; never performs HTTP."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('body', type=Path)
    args = p.parse_args()
    raw = args.body.read_bytes()
    if raw.startswith(b'\x1f\x8b'):
        raw = gzip.decompress(raw)
    data = json.loads(raw); s = data['compSchedules']
    if len(s['keys']) != len(set(s['keys'])) or any(len(r) != len(s['keys']) for r in s['datas']):
        raise ValueError('invalid detail envelope')
    counts = Counter()
    for values in s['datas']:
        row = dict(zip(s['keys'], values))
        if row.get('leagueCode') == 'BS001':
            key = tuple(row.get(k) for k in ('betId','betTypId','betNm','betTypNm','winTxt','drawTxt','loseTxt'))
            counts[key] += 1
    print(json.dumps({'round': data['gmTs'], 'sha256': hashlib.sha256(raw).hexdigest(), 'products': [{'terms': k, 'count': v} for k, v in counts.items()], 'eligible_offers': 0, 'eligibility': 'not evaluated: receipt provenance and official fixtures required'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
