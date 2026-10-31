"""Bounded local queue CLI; message sends use Codex tools with a fixed registry."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.research_dispatch import build_manifest, publish, claim, transition, status, dispatch_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('publish')
    p.add_argument('--adapter-inputs', type=Path, required=True)
    p.add_argument('--seed', action='store_true', help='Record existing data as baseline without dispatch')
    p = commands.add_parser('claim')
    p.add_argument('--limit', type=int, default=3)
    p = commands.add_parser('prepare')
    p.add_argument('--event-id', required=True)
    commands.add_parser('status')
    p = commands.add_parser('transition')
    p.add_argument('--event-id', required=True)
    p.add_argument('--state', choices=('dispatched','uncertain','pending','completed'), required=True)
    p.add_argument('--receipt', type=Path)
    p.add_argument('--operator-checked', action='store_true')
    args = parser.parse_args()
    if args.command == 'publish':
        if args.adapter_inputs.stat().st_size > 8*1024*1024:
            raise ValueError('manifest batch exceeds 8 MiB; split upstream')
        inputs = json.loads(args.adapter_inputs.read_text(encoding='utf-8-sig'))
        if not isinstance(inputs,list) or len(inputs)>1000:
            raise ValueError('expected at most 1000 adapter inputs')
        result = publish(args.state_dir, build_manifest(inputs),seed=args.seed)
    elif args.command == 'claim':
        result = claim(args.state_dir, limit=args.limit)
    elif args.command == 'status':
        result = status(args.state_dir)
    elif args.command == 'prepare':
        matches=[j for j in status(args.state_dir) if j['event_id']==args.event_id]
        if len(matches)!=1: raise ValueError('event not unique')
        result=dispatch_request(args.state_dir,matches[0])
    else:
        receipt = json.loads(args.receipt.read_text(encoding='utf-8-sig')) if args.receipt else None
        result = transition(args.state_dir, args.event_id, args.state,
                            receipt=receipt, operator_checked=args.operator_checked)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
