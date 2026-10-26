"""Export bounded research pages for offline comparison; never wager or promote."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pregame_research_store import PregameResearchStore


def export(path, stream):
    # Do not create a new DB when the caller misspells an input path.
    path = Path(path).resolve(strict=True)
    store = PregameResearchStore(path, official_sources={"proto_official"}, read_only=True)
    count = complete = cursor = 0
    while True:
        page_count = 0
        for row in store.export_paired(after_id=cursor, limit=100):
            cursor = row["pair"]["id"]
            page_count += 1
            count += 1
            complete += int(row["complete"])
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        if not page_count:
            break
    return {"pairs": count, "complete": complete, "incomplete": count-complete,
            "monthly_loss_limit_krw": 50000, "model_effect_roi": None,
            "status": "exported_not_validated", "real_wagering": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("output", type=Path, help="New JSONL file; cannot overwrite")
    args = parser.parse_args()
    if args.database.resolve() == args.output.resolve():
        parser.error("input and output must differ")
    with args.output.open("x", encoding="utf-8") as stream:
        summary = export(args.database, stream)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
