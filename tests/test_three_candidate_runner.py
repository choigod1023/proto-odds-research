"""Settlement, abstention and embargo checks independent of model internals."""
from datetime import date,timedelta
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1];HERE=ROOT/'experiments/kleague-three-candidates-20261101'
spec=importlib.util.spec_from_file_location('three_runner',HERE/'run.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class RecordingExpert:
    def __init__(self):self.calls=[]
    def fit(self,rows,reference_day):
        cutoff=(date.fromisoformat(reference_day)-timedelta(days=7)).isoformat()
        assert all(r['day']<=cutoff for r in rows)
        self.calls.append((reference_day,tuple(r['id'] for r in rows)))
        return dict(cutoff_inclusive=cutoff,training_rows=len(rows))
    def predict(self,state,row):
        assert not {'y','hg','ag','forecasts','split'}&set(row)
        return row['market_p'],{}


class Runner(unittest.TestCase):
    def test_every_month_same_cohort_embargo_and_no_outcome_to_prediction(self):
        rows=[json.loads(s) for s in (HERE/'oof.jsonl').read_text(encoding='utf8').splitlines()]
        expert=RecordingExpert();ledger,states=runner.evaluate(rows,{'probe':expert})
        self.assertEqual(len(ledger),227);self.assertEqual(len(states),10)
        self.assertEqual(runner.metrics(ledger,'probe'),runner.metrics(ledger,'market'))
        self.assertEqual(runner.metrics(ledger,'probe')['hits'],99)
        self.assertEqual(runner.metrics(ledger,'probe')['net_units'],'-33.34')
        for reference,ids in expert.calls:
            expected={r['id'] for r in rows if r['day']<=(date.fromisoformat(reference)-timedelta(days=7)).isoformat()}
            self.assertEqual(set(ids),expected)

    def test_ties_abstain_and_probabilities_checked(self):
        self.assertIsNone(runner.selection([.4,.4,.2]))
        self.assertEqual(runner.selection([.40001,.39999,.2]),0)
        for p in ([float('nan'),.5,.5],[0,.5,.5],[.5,.3]):
            with self.assertRaises(ValueError):runner.selection(p)


if __name__=='__main__':unittest.main()
