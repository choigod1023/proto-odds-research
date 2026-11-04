"""Exploratory paired day-block intervals, not a promotion or profit guarantee."""
from collections import defaultdict
from datetime import datetime
import gzip
import json
from pathlib import Path
import random
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def percentile(values, q):
    values = sorted(values)
    pos = (len(values)-1)*q
    lo = int(pos)
    return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(pos-lo)


def main():
    study = json.loads(gzip.decompress((ROOT / "observed-quotes/results.json.gz").read_bytes()))
    main = study["all"]["common_unique_vote_cohort"]
    original = {r["snapshot_id"]: r for r in main["results"]["frozen_vote"]["rows"]}
    report = dict(seed=20261104, replicates=10000, stake=10000,
                  timing_warning="Legacy quote batch-start timestamps; completed HTTP receipt before T30 is not proven.",
                  note="Retrospective day-block sensitivity. Handles may correlate across days; no external timing/execution authentication.")
    for rule, stats in main["results"].items():
        if rule in {"frozen_vote", "vote_market_agreement"}:
            continue
        daily = defaultdict(lambda: [0, 0, 0.])
        for row in stats["rows"]:
            baseline = original[row["snapshot_id"]]
            day = datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
            daily[day][0] += 1
            daily[day][1] += int(row["hit"])-int(baseline["hit"])
            daily[day][2] += float(row["pnl_won"])-float(baseline["pnl_won"])
        blocks = [daily[k] for k in sorted(daily)]
        rng, accuracy, roi = random.Random(20261104), [], []
        for _ in range(10000):
            selected = rng.choices(blocks, k=len(blocks))
            count = sum(v[0] for v in selected)
            accuracy.append(100*sum(v[1] for v in selected)/count)
            roi.append(100*sum(v[2] for v in selected)/(10000*count))
        report[rule] = dict(days=len(blocks), fixtures=len(original),
                            accuracy_delta_pp=stats["accuracy_percent"]-main["results"]["frozen_vote"]["accuracy_percent"],
                            accuracy_delta_day_block_95percentile=[percentile(accuracy,.025),percentile(accuracy,.975)],
                            roi_delta_pp=stats["roi_percent"]-main["results"]["frozen_vote"]["roi_percent"],
                            roi_delta_day_block_95percentile=[percentile(roi,.025),percentile(roi,.975)])
    (ROOT / "uncertainty.json").write_bytes((json.dumps(report,ensure_ascii=False,indent=2)+"\n").encode())
    print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__":
    main()
