import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from scripts import local_market_information as m


def row(i=0):
    return dict(match_id=str(i),league='D1',season=23,date='2023-09-01',y=0,prices=[1.5,4.,7.],
                books={'PS':[1.6,4.,6.],'BW':[1.7,3.5,5.],'WH':[1.6,3.8,5.5]})


class MarketInformationTests(unittest.TestCase):
    def assertArtifactEqual(self, actual, expected):
        if isinstance(expected,float):
            self.assertAlmostEqual(actual,expected,delta=1e-12)
        elif isinstance(expected,dict):
            self.assertEqual(actual.keys(),expected.keys())
            for key in expected:self.assertArtifactEqual(actual[key],expected[key])
        elif isinstance(expected,list):
            self.assertEqual(len(actual),len(expected))
            for a,b in zip(actual,expected):self.assertArtifactEqual(a,b)
        else:self.assertEqual(actual,expected)

    def test_target_exclusion_and_external_information(self):
        rows=[row()]; original=m.predict(rows)
        rows[0]['prices']=[5.,4.,1.6]
        rows[0]['books']['B365']=[9.,9.,1.1]
        rows[0]['books']['PSC']=[9.,9.,1.1]
        rows[0]['books']['Avg']=[9.,9.,1.1]
        rows[0]['books']['Max']=[9.,9.,1.1]
        after=m.predict(rows)
        for name in ('ps','consensus_mean','consensus_median'): np.testing.assert_array_equal(original[name],after[name])
        self.assertFalse(np.allclose(original['b365'],after['b365']))
        rows[0]['books']['PS']=[5.,3.,2.]
        self.assertFalse(np.allclose(after['consensus_mean'],m.predict(rows)['consensus_mean']))

    def test_outcome_free_and_normalized(self):
        rows=[row()]; a=m.predict(rows)
        del rows[0]['y']
        for name,p in m.predict(rows).items():
            np.testing.assert_array_equal(a[name],p)
            np.testing.assert_allclose(p.sum(1),1.)

    def test_missing_markets_and_coverage(self):
        r=row(); del r['books']['PS']; self.assertFalse(m.available(r))
        r=row(); del r['books']['BW']; self.assertFalse(m.available(r))
        r=row(); r['prices']=None; self.assertFalse(m.available(r))
        with self.assertRaises(ValueError): m.enforce_coverage([row(),r])
        m.enforce_coverage([row(i) for i in range(19)]+[r])

    def test_audit_never_needs_outcome_fields(self):
        text='HomeTeam,B365H,B365D,B365A,PSH,PSD,PSA,BWH,BWD,BWA,WHH,WHD,WHA\nA,2,3,4,2,3,4,2,3,4,2,3,4\n'
        audit=m.price_audit(text.encode())
        self.assertEqual(audit['common'],1)
        self.assertEqual(m.prices({'PSCH':2,'PSCD':3,'PSCA':4},'PS'),None)

    def test_inner_only_and_chronology(self):
        metrics={name:{'logloss':i+1.} for i,name in enumerate(m.MODELS)}
        trials={'b365/highestprob':{'tickets':25,'budget_return':-.1},'ps/ev02':{'tickets':21,'budget_return':.1}}
        self.assertEqual(m.choose_inner('inner',metrics,trials)['policy'],'ps/ev02')
        with self.assertRaises(ValueError): m.choose_inner('outer',metrics,trials)
        with self.assertRaises(ValueError): m.check_dates([row()],[row(1)])
        before=row(); before['date']='2022-09-01'; m.check_dates([before],[row()])

    def test_target_settlement_distinct_games_and_cash_budgets(self):
        rows=[row(0),row(1)]; p=m.predict(rows)['consensus_mean']
        tickets=m.choose_tickets([{k:v for k,v in r.items() if k!='y'} for r in rows],p,'highestprob')
        record=m.settle(rows,tickets)[0]
        self.assertAlmostEqual(record['profit'],1.5*1.5-1)
        self.assertEqual(len({a['match_id'] for a in record['legs']}),2)
        cash=m.settle(rows,{})[0]; self.assertEqual(cash['budget'],record['budget']); self.assertEqual(cash['profit'],0.)
        a=record['legs'][0]
        with self.assertRaises(ValueError):m.settle(rows,{('D1','2023-09-01'):[a,a]})

    def test_parser_validation(self):
        header='Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n'
        body=''.join(f'D1,01/09/2023,H{i},A{i},1,0,H,2,3,4\n' for i in range(306))
        self.assertEqual(len(m.parse_source((header+body).encode(),'D1',23)),306)
        with self.assertRaises(ValueError):m.parse_source((header+body.replace('1,0,H','1,0,D',1)).encode(),'D1',23)
        duplicate=body.replace('H305,A305','H0,A0')
        with self.assertRaisesRegex(ValueError,'duplicate'):m.parse_source((header+duplicate).encode(),'D1',23)

    def test_portable_text_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            lf,crlf=Path(tmp)/'lf',Path(tmp)/'crlf'
            lf.write_bytes(b'alpha\nbeta\n'); crlf.write_bytes(b'alpha\r\nbeta\r\n')
            self.assertEqual(m.text_sha256(lf),m.text_sha256(crlf))

    def test_saved_hashes_and_pooled_artifacts(self):
        result=json.loads((m.ROOT/'docs/research/2026-10-01-market-information-results.json').read_text(encoding='utf-8'))
        self.assertEqual(result['code_sha256'],m.text_sha256(m.__file__))
        self.assertEqual(result['protocol_sha256'],m.PROTOCOL_SHA256)
        self.assertEqual(result['protocol_sha256'],m.text_sha256(m.ROOT/m.PROTOCOL))
        for path,sha in result['dependency_sha256'].items():
            self.assertNotIn('\\',path)
            self.assertEqual(sha,m.text_sha256(m.ROOT/path))
        for group,pooled in result['pooled'].items():
            forecasts=[r for f in result['folds'] for r in f['outer'][group]['forecasts']]
            for name,expected in pooled['policy'].items():
                records=[r for f in result['folds'] for r in f['outer'][group]['records'][name]]
                self.assertArtifactEqual(m.ledger_summary(records),expected)
            for model,expected in pooled['models'].items():
                probs=np.array([r['probabilities'][model] for r in forecasts])
                self.assertArtifactEqual(m.probability_metrics(forecasts,probs),expected)
            probs=np.array([r['probabilities'][f['winner']['probability']] for f in result['folds'] for r in f['outer'][group]['forecasts']])
            self.assertArtifactEqual(m.probability_metrics(forecasts,probs),pooled['selected_probability'])

    def test_saved_inner_choices_and_native_odds_settlement(self):
        path=m.ROOT/'docs/research/2026-10-01-market-information-results.json'
        if not path.exists(): self.skipTest('No saved experiment yet')
        result=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(result['unique_trials'],20)
        self.assertEqual(len(result['sources']),18)
        for fold in result['folds']:
            self.assertEqual(m.choose_inner('inner',fold['inner_models'],fold['inner_trials']),fold['winner'])
            for group in fold['outer'].values():
                self.assertEqual(len(group['trials']),20)
                actual={r['match_id']:r for r in group['settlement_rows']}
                self.assertEqual(len(actual),group['coverage']['raw'])
                for forecast in group['forecasts']:
                    for key in ('y','prices','date','league'):
                        self.assertEqual(forecast[key],actual[forecast['match_id']][key])
                for name,records in group['records'].items():
                    self.assertArtifactEqual(m.ledger_summary(records),group['policy'][name])
                    for r in records:
                        if not r['stake']: self.assertEqual(r['profit'],0.); continue
                        a,b=r['legs']
                        self.assertNotEqual(a['match_id'],b['match_id'])
                        for leg in r['legs']:
                            source=actual[leg['match_id']]
                            self.assertEqual(leg['y'],source['y'])
                            self.assertEqual(leg['odds'],source['prices'][leg['outcome']])
                            self.assertEqual((r['league'],r['date']),(source['league'],source['date']))
                        won=int(all(leg['outcome']==leg['y'] for leg in r['legs']))
                        self.assertEqual(r['won'],won)
                        profit=Decimal(str(a['odds']))*Decimal(str(b['odds']))*won-1
                        self.assertAlmostEqual(r['profit'],float(profit))


if __name__=='__main__':unittest.main()
