"""Bounded local queue CLI; message sends use Codex tools with a fixed registry."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.research_dispatch import build_manifest, publish, claim, transition, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('publish')
    p.add_argument('--adapter-inputs', type=Path, required=True)
    p = commands.add_parser('claim')
    p.add_argument('--limit', type=int, default=3)
    commands.add_parser('status')
    p = commands.add_parser('transition')
    p.add_argument('--event-id', required=True)
    p.add_argument('--state', choices=('dispatched','uncertain','pending','completed'), required=True)
    p.add_argument('--receipt', type=Path)
    p.add_argument('--operator-checked', action='store_true')
    args = parser.parse_args()
    if args.command == 'publish':
        inputs = json.loads(args.adapter_inputs.read_text(encoding='utf-8-sig'))
        result = publish(args.state_dir, build_manifest(inputs))
    elif args.command == 'claim':
        result = claim(args.state_dir, limit=args.limit)
    elif args.command == 'status':
        result = status(args.state_dir)
    else:
        receipt = json.loads(args.receipt.read_text(encoding='utf-8-sig')) if args.receipt else None
        result = transition(args.state_dir, args.event_id, args.state,
                            receipt=receipt, operator_checked=args.operator_checked)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
