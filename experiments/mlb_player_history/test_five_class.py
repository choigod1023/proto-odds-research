from collections import Counter
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlencode

import five_class as f
from pilot import FIELDS, digest, PERIODS


def row(day='2025-06-01', game=1, outcome='K'):
    return dict(day=day,game=game,pa=0,batter=1,pitcher=2,outcome=outcome)


def stat(group='hitting'):
    result = dict.fromkeys(FIELDS,0)
    result.update(atBats=8,hits=3,homeRuns=1,strikeOuts=2,baseOnBalls=1,hitByPitch=1)
    result['plateAppearances' if group=='hitting' else 'battersFaced'] = 10
    return result


class FiveClassTests(unittest.TestCase):
    def test_exact_coarsening_preserves_all_rows(self):
        original = [row(game=i,outcome=k) for i,k in enumerate(f.COARSEN)]
        result = f.coarsen(original)
        self.assertEqual(len(result),6)
        self.assertEqual(Counter(r['outcome'] for r in result),Counter({'K':1,'BB+HBP':1,'HR':1,'nonHRhit':1,'otherPA':2}))
        self.assertEqual([r['original_outcome'] for r in result],list(f.COARSEN))
        self.assertEqual([r['game'] for r in original],[r['game'] for r in result])
        with self.assertRaises(KeyError):
            f.coarsen([row(outcome='unknown')])

    def test_historical_exhaustive_counts(self):
        for group in ('hitting','pitching'):
            c = f.counts(stat(group),group)
            self.assertEqual(c,Counter({'K':2,'BB+HBP':2,'HR':1,'nonHRhit':2,'otherPA':3}))
            self.assertEqual(c.total(),10)
        bad = stat()
        bad['plateAppearances'] = 11
        with self.assertRaises(ValueError):
            f.counts(bad,'hitting')

    def test_no_same_day_or_future_day_leakage(self):
        rows = [row(),row('2025-06-02',2,'HR'),row('2025-06-02',3,'otherPA')]
        seed = Counter({'K':20,'otherPA':80})
        original_seed = copy.deepcopy(seed)
        pred = f.evaluate(rows,seed,{1:seed},{2:seed},'seeded')
        changed = copy.deepcopy(rows)
        changed[1]['outcome'] = 'K'
        changed += [row('2025-06-03',4,'BB+HBP')]
        revised = f.evaluate(changed,seed,{1:seed},{2:seed},'seeded')
        self.assertEqual([r['probabilities'] for r in pred],[r['probabilities'] for r in revised[:len(pred)]])
        today = [r for r in pred if r['day']=='2025-06-02']
        self.assertTrue(all(r['prior_pa']==101 and r['batter_history']==101 for r in today))
        self.assertEqual(seed,original_seed)
        self.assertEqual(pred,f.evaluate(list(reversed(rows)),seed,{1:seed},{2:seed},'seeded'))
        self.assertTrue(all(r['phase']=='warmup' for r in pred[:2]))

    def test_exact_strength_and_missing_player_fallback(self):
        seed = Counter({'K':20,'otherPA':80})
        pred = f.evaluate([row()],seed,{1:Counter({'K':10})},{},'seeded')
        prior = 21/105
        self.assertAlmostEqual(pred[0]['probabilities'][0],prior)
        self.assertAlmostEqual(pred[1]['probabilities'][0],0.5*((10+100*prior)/110+prior))
        cold = f.evaluate([row()],Counter(),{},{},'cold')
        self.assertEqual(cold[0]['probabilities'],[0.2]*5)
        self.assertAlmostEqual(cold[0]['brier'],0.8)

    def test_seed_population_cutoff_integrity_and_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'history.sqlite'
            conn = sqlite3.connect(path)
            conn.executescript('CREATE TABLE analysis(id INTEGER,detail TEXT); CREATE TABLE raw(id INTEGER,url TEXT,url_sha256 TEXT,body BLOB,body_sha256 TEXT,status INTEGER);')
            conn.execute('INSERT INTO analysis VALUES(1,?)',(json.dumps({'sampled_date_checks_passed':True}),))
            for ident,(group,start,end) in enumerate((g,s,e) for g in ('hitting','pitching') for s,e in PERIODS):
                url = 'https://statsapi.mlb.com/api/v1/stats?'+urlencode(dict(group=group,startDate=start,endDate=end,offset=0,stats='byDateRange',gameType='R',sportIds=1))
                body = json.dumps({'stats':[dict(type=dict(displayName='byDateRange'),group=dict(displayName=group),totalSplits=1,
                    splits=[dict(player=dict(id=1),sport=dict(id=1),season=start[:4],stat=stat(group))])]}).encode()
                conn.execute('INSERT INTO raw VALUES(?,?,?,?,?,200)',(ident,url,digest(url.encode()),body,digest(body)))
            conn.commit()
            league,b,p,audit,receipts = f.seeds(path)
            self.assertEqual(league.total(),20)  # Never 40 from counting both sides.
            self.assertEqual(b[1].total(),20)
            with f.readonly(path) as ro:
                with self.assertRaises(sqlite3.OperationalError):
                    ro.execute('DELETE FROM raw')
            ro.close()
            url = conn.execute('SELECT url FROM raw WHERE id=0').fetchone()[0].replace('2024-12-31','2025-06-01')
            conn.execute('UPDATE raw SET url=?,url_sha256=? WHERE id=0',(url,digest(url.encode())))
            conn.commit()
            with self.assertRaisesRegex(ValueError,'cutoff'):
                f.seeds(path)
            conn.close()


if __name__ == '__main__':
    unittest.main()
