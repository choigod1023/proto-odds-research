"""Replay archived comparison results without fitting or changing evidence."""
import importlib.util
import json
from pathlib import Path
import unittest
import numpy as np

HERE=Path(__file__).resolve().parents[1]/'experiments/kleague-three-candidates-20261101'
spec=importlib.util.spec_from_file_location('candidate_replay',HERE/'verify.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


class Replay(unittest.TestCase):
    def test_all_saved_bayes_probabilities_match_independent_poisson_sum(self):
        ledger=[json.loads(s) for s in (HERE/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
        states=json.loads((HERE/'states.json').read_text(encoding='utf8'))
        self.assertEqual(len(ledger),227)
        for row in ledger:
            state=next(s['state'] for s in states if s['model']=='bayes_market_goal' and s['reference_day']==row['meta_reference'])
            independent=replay.bayes_poisson_replay(state,row)
            np.testing.assert_allclose(independent,row['forecasts']['bayes_market_goal']['p'],atol=1e-10,rtol=0)

    def test_no_posthoc_code_change_relative_to_registration(self):
        spec=importlib.util.spec_from_file_location('candidate_runner',HERE/'run.py')
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        result=json.loads((HERE/'results.json').read_text(encoding='utf8'))
        # Shallow CI checkouts may not have the ancestor object. Frozen canonical
        # hashes still verify the exact source/inputs in that checkout.
        for path,expected in result['source_lf_sha256'].items():
            self.assertEqual(runner.digest(HERE.parents[1]/path),expected,path)
        self.assertFalse(result['adopted']);self.assertFalse(result['formal_validation'])


if __name__=='__main__':unittest.main()
