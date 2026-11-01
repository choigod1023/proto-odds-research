"""The comparison starts from complete, embargoed, hash-bound OOF inputs."""
import hashlib
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
import unittest
import numpy as np
from scipy.stats import skellam

ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/'experiments/kleague-three-candidates-20261101'
spec=importlib.util.spec_from_file_location('prepare_three',HERE/'prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class Inputs(unittest.TestCase):
    def test_separate_parser_preserves_actual_year(self):
        row=dict(year='2024',sport='sc',league='K리그1',market_family='승무패',market_label='',
                 n_way='3',home='HOME 2',away='1 AWAY',date_text='03.01(금) 14:00')
        self.assertEqual(prepare.parse_2024(row)['day'],'2024-03-01')
        self.assertIsNone(prepare.parse_2024({**row,'year':'2025'}))
        with self.assertRaises(ValueError):prepare.parse_2024({**row,'date_text':'bad'})

    def test_inputs_complete_and_time_separated(self):
        rows=[json.loads(s) for s in (HERE/'oof.jsonl').read_text(encoding='utf8').splitlines()]
        self.assertEqual(len(rows),455)
        self.assertEqual(len({r['id'] for r in rows}),455)
        self.assertEqual(sum(r['split']=='meta_history_2024' for r in rows),228)
        manifest=json.loads((HERE/'input-manifest.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['features_sha256'],hashlib.sha256((HERE/'oof.jsonl').read_bytes()).hexdigest())
        self.assertFalse(manifest['price_receipt_verified'])
        states=json.loads((HERE/'base-states.json').read_text(encoding='utf8'))
        for r in rows:
            cutoff=(date.fromisoformat(r['dc_reference'])-timedelta(days=7)).isoformat()
            self.assertEqual(r['dc_training_cutoff'],cutoff)
            self.assertLess(cutoff,r['day'])
            state=next(s for s in states[r['split']] if s['reference_day']==r['dc_reference'])
            self.assertEqual(state['cutoff_inclusive'],cutoff)
            lh,la=r['market_lh'],r['market_la']
            p=[skellam.sf(0,lh,la),skellam.pmf(0,lh,la),skellam.cdf(-1,lh,la)]
            np.testing.assert_allclose(p,r['market_p'],atol=1e-6,rtol=0)
            self.assertEqual(r['y'],0 if r['hg']>r['ag'] else 1 if r['hg']==r['ag'] else 2)

    def test_inversion_not_outcome_dependent(self):
        p=[float(skellam.sf(0,1.8,.9)),float(skellam.pmf(0,1.8,.9)),float(skellam.cdf(-1,1.8,.9))]
        rates,error=prepare.implied_rates(p)
        np.testing.assert_allclose(rates,[1.8,.9],atol=1e-6,rtol=0)
        self.assertLess(error,1e-6)


if __name__=='__main__':unittest.main()
