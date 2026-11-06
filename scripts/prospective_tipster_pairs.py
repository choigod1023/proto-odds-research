"""One local capture per invocation and an offline paired settlement report."""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

from scripts import collect_proto_comparison_prices as proto
from scripts import collect_tipster_comparison_sources as tipsters
from src.prospective_tipster_pairs import evaluate, round_id

CAPTURE_SET = "paired-v1"


def capture(round_number, evidence, *, article_requests=8):
    """Archive articles first. A failed official request leaves them usable."""
    round_id(round_number)
    if not 0 <= article_requests <= 8:
        raise ValueError("article_request_cap_must_be_0_to_8")
    root = evidence / CAPTURE_SET
    directory = root / round_number
    directory.mkdir(parents=True, exist_ok=True)
    capture_dir = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=directory))
    stop_file = root / "STOP_ARTICLES_403_429.json"
    summary = dict(round=round_number, capture_dir=str(capture_dir), articles=dict(status="skipped"))
    if article_requests and not stop_file.exists():
        try:
            report = tipsters.run(SimpleNamespace(output=capture_dir / "articles", prices=[],
                                                  evidence=capture_dir / "article-evidence", capture_id=capture_dir.name,
                                                  max_requests=article_requests),
                                  listings=(tipsters.LISTINGS[0], tipsters.LISTINGS[-1]), articles_per_listing=2,
                                  stop_file=stop_file)
            summary["articles"] = dict(report, status="blocked" if report["access_blocked"] else "captured")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            summary["articles"] = dict(status="pending", reason=str(exc))
    elif article_requests:
        summary["articles"] = dict(status="blocked", reason="previous_article_403_429")
    try:
        price_root = capture_dir / "price-evidence"
        state = proto.run(SimpleNamespace(start=date.min.isoformat(), end=date.max.isoformat(),
                                          round=round_number, evidence=price_root, capture_id=capture_dir.name,
                                          schema="v2", max_requests=1, total_timeout=20, page_size=1))
        summary["official"] = state["events"][-1]
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        summary["official"] = dict(status="pending", reason=str(exc))
    proto.write_json(capture_dir / "capture.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    for command in ("capture", "evaluate"):
        mode = modes.add_parser(command)
        mode.add_argument("--round", required=True, type=round_id, help="Explicit six-digit Betman G101 round")
        mode.add_argument("--evidence", required=True, type=Path, help="Local evidence root")
    modes.choices["capture"].add_argument("--article-requests", type=int, choices=range(9), default=8,
                                          help="0 for a one-request Betman result refresh")
    offline = modes.choices["evaluate"]
    offline.add_argument("--article-root", type=Path, action="append", default=[],
                         help="Additional existing article gzip/sidecar directory, repeatable")
    offline.add_argument("--price-root", type=Path, action="append", default=[],
                         help="Additional Betman collector capture directory, repeatable")
    offline.add_argument("--evaluated-at", default=None, help="Aware ISO timestamp; defaults to current UTC")
    offline.add_argument("--ledger", type=Path, help="Optional local exported prediction-ledger JSONL")
    args = parser.parse_args()
    if args.command == "capture":
        report = capture(args.round, args.evidence, article_requests=args.article_requests)
    else:
        ledger_records = None
        if args.ledger is not None:
            if os.environ.get("PROODD_DB_PATH") or not args.ledger.is_file():
                parser.error("--ledger requires an existing local export and unset PROODD_DB_PATH")
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
            from prediction_ledger import PredictionLedger
            ledger_records = PredictionLedger(args.ledger).records()
        directory = args.evidence / CAPTURE_SET / args.round
        report = evaluate(args.round, capture_dirs=sorted(path for path in directory.glob("*/") if path.is_dir()),
                          article_roots=args.article_root, price_roots=args.price_root, ledger_records=ledger_records,
                          evaluated_at=args.evaluated_at or datetime.now(timezone.utc).isoformat())
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
