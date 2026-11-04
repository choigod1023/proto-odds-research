import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "experiments/vote-accuracy-same-period-20261104"


def run(tmp_path, mutation=None):
    for name in ("inputs.json", "labels.json", "input-manifest.json", "protocol.json"):
        shutil.copyfile(STUDY / name, tmp_path / name)
    if mutation:
        path = tmp_path / mutation
        data = json.loads(path.read_bytes())
        if mutation == "inputs.json":
            data[0]["options"][0]["odds"] = 999
        else:
            data["market_weight_fixed"] = "0.75"
        path.write_text(json.dumps(data), encoding="utf8")
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/compare_frozen_vote_rules.py"),
                           "evaluate", "--directory", str(tmp_path)],
                          capture_output=True, text=True, encoding="utf8", timeout=20)


def test_real_frozen_cohort_reproduces_reference_and_alternate_prices(tmp_path):
    process = run(tmp_path)
    assert process.returncode == 0, process.stderr
    study = json.loads((tmp_path / "results.json").read_bytes())
    base, market, hybrid, agreement = (study["results"][name] for name in
                                     ("frozen_vote", "market_same_product", "market90_vote10", "vote_market_agreement"))
    assert (base["bets"], base["hit"], base["roi_percent"]) == (14, 8, -4.5)
    assert (market["bets"], market["hit"], market["roi_percent"]) == (14, 9, 6.0)
    assert market["rows"] == hybrid["rows"]
    assert (agreement["bets"], agreement["hit"], agreement["net_won"]) == (13, 8, "3700.00")
    assert study["paired"]["market_same_product"]["changed_pick_count"] == 1
    change = next(row for row in market["rows"] if row["snapshot_id"] == "dec_2410bbf1e823cf7a")
    assert (change["selection"], change["odds"], change["pnl_won"]) == ("홈", 1.47, "4700.00")


@pytest.mark.parametrize("mutation", ["inputs.json", "protocol.json"])
def test_tampered_input_or_protocol_never_produces_a_result(tmp_path, mutation):
    process = run(tmp_path, mutation)
    assert process.returncode != 0
    assert not (tmp_path / "results.json").exists()
    assert "changed" in process.stderr
