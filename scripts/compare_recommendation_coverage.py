"""Descriptive external-data sensitivity check; not a new independent holdout.

Reuses the pinned EPL dataset from the earlier temporal experiment. Only market
probabilities and quoted prices select the favorite; results only settle bets.
Foreign prices lack exact observation times and cannot prove Proto returns.
One unit per single, no combinations, fees or simulated execution.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_external_temporal_roi import download
from src.devig import shin


def compare(rows):
    output = {}
    for season in ("2023-24", "2024-25"):
        eligible = []
        total = 0
        for row in rows:
            if row["season"] != season or not row["prices"]:
                continue
            total += 1
            probabilities = shin(row["prices"])
            i = max(range(3), key=lambda index: probabilities[index])
            if 1.5 <= row["prices"][i] < 2.2:
                eligible.append((probabilities[i], row["prices"][i], row["y"] == i))
        output[season] = {}
        for threshold in (.60, .58, .55):
            selected = [(p, o, hit) for p, o, hit in eligible if p >= threshold]
            n = len(selected)
            output[season][str(threshold)] = {
                "matches": total, "picks": n, "coverage": n / total if total else None,
                "hit_rate": sum(hit for _, _, hit in selected) / n if n else None,
                "realized_single_roi": sum(o * hit - 1 for _, o, hit in selected) / n if n else None,
            }
    return output


if __name__ == "__main__":
    rows, hashes, _ = download()
    print(json.dumps({"retrospective_not_independent": True,
                      "source_hashes": hashes, "results": compare(rows)}, indent=2))
