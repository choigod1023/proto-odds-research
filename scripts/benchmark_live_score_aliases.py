"""Local fixture comparison; never fetches feeds or changes the runtime DB."""
import ast
from copy import deepcopy
import difflib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import live_scores


def main():
    source = subprocess.check_output(
        ["git", "show", "84ae6c54:src/live_scores.py"], cwd=ROOT, encoding="utf-8")
    functions = {"_team_key", "_team_similarity", "_team_similarity_with_aliases", "add_proto_aliases"}
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in functions]
    namespace = {"re": re, "difflib": difflib}
    exec(compile(tree, "baseline", "exec"), namespace)
    named = json.loads((ROOT / "docs/data/live_scores.json").read_text(encoding="utf-8"))["games"]
    picks = json.loads((ROOT / "docs/data/picks_v2.json").read_text(encoding="utf-8"))
    proto = [*picks.get("live", []), *picks.get("past", [])]
    expected = None
    for label, function in [("baseline", namespace["add_proto_aliases"]),
                            ("optimized", live_scores.add_proto_aliases)]:
        timings = []
        for _ in range(3):
            games = deepcopy(named)
            start = time.perf_counter()
            matched = function(games, proto)
            timings.append(time.perf_counter() - start)
            result = (matched, games)
            if expected is None:
                expected = result
            assert result == expected, "Matching output changed"
        print(json.dumps({"version": label, "seconds": timings,
                          "median_seconds": statistics.median(timings), "matched": matched,
                          "named_count": len(named), "proto_count": len(proto), "equal": True}))


if __name__ == "__main__":
    main()
