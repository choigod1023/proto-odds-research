import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.audit_recommendation_shadow import stamp
from scripts.run_forward_paper import (
    ACCOUNT, LEGACY_PROTOCOL, POLICY, PROTOCOL, advance, budget_summary,
    identity, paper_report, settlement_row, ticket_summary,
)

NOW = stamp('2026-10-28T10:01:00+09:00')


def source(now=NOW, count=2):
    history = {}
    for i in range(count):
        at = (now-timedelta(minutes=1)).isoformat()
        history[str(i)] = dict(
            id=str(i), recorded_at=at, published_at=at,
            kickoff_at=(now+timedelta(hours=4)).isoformat(),
            sport='sc', league=f'league-{i}', home=f'home-{i}', away='away',
            round=1, market='승무패', market_label='', n_way=3, sel='무',
            odds=1.49, probability=.3, recommended=False,
            policy='daily-p55-o150-unbounded-v4', selection_policy=POLICY,
            result='unknown', settled_at=None,
            tipster_selection=dict(version=POLICY, status='applied', selected_side='무',
                authors=3, vote_counts={'홈': 1, '무': 2, '원정': 0},
                vote_shares={'홈': 1/3, '무': 2/3, '원정': 0},
                evidence_hash=f'evidence-{i}', latest_observed_at=at))
    return dict(generated_at=now.isoformat(), recommendation_history=history)


def legacy_bet(account='probability-weekly2', at=NOW-timedelta(days=1), index=0):
    rows = list(source(at)['recommendation_history'].values())
    legs = []
    for row in rows:
        row.pop('selection_policy'); row.pop('tipster_selection')
        row['odds'] = 1.8
        legs.append(identity(row))
    return dict(type='bet', protocol=LEGACY_PROTOCOL, at=at.isoformat(),
                ticket=f'{account}:{index}', account=account, month=at.strftime('%Y-%m'),
                stake=10000, legs=legs)


def resolve(doc, at, result='hit'):
    for row in doc['recommendation_history'].values():
        row.update(result=result, result_source='official', settled_at=at.isoformat())


class ForwardTests(unittest.TestCase):
    def test_one_primary_singles_no_highlight_gate_or_probability_rewrite(self):
        doc=source(); original=copy.deepcopy(doc)
        events, added=advance([], doc, NOW, 'digest')
        bets=[e for e in added if e['type']=='bet']
        self.assertEqual(len(bets),2)
        self.assertEqual({e['account'] for e in bets},{ACCOUNT})
        self.assertTrue(all(len(e['legs'])==1 for e in bets))
        self.assertEqual(bets[0]['legs'][0]['probability'],.3)
        self.assertEqual(bets[0]['legs'][0]['tipster_selection']['vote_shares']['무'],2/3)
        self.assertTrue(all(e['source_sha256']=='digest' for e in bets))
        self.assertEqual(doc,original)
        self.assertEqual(advance(events,doc,NOW)[1],[])

    def test_single_payout_exact_odds_and_void(self):
        for result,expected in [('hit',4900),('miss',-10000),('void',0)]:
            with self.subTest(result=result):
                doc=source(count=1); events,_=advance([],doc,NOW)
                old=copy.deepcopy(events)
                resolve(doc,NOW+timedelta(hours=6),result)
                done,_=advance(events,doc,NOW+timedelta(hours=7))
                self.assertEqual(done[:len(old)],old)
                self.assertEqual(ticket_summary(done,ACCOUNT)['net'],expected)
                self.assertEqual(advance(done,doc,NOW+timedelta(hours=7))[1],[])

    def test_legacy_immutable_settlement_and_no_more_legacy_placements(self):
        old=[legacy_bet()]
        doc=source()
        for i,leg in enumerate(old[0]['legs']):
            doc['recommendation_history'][f'legacy-{i}']=dict(leg,result='hit',result_source='official',settled_at=NOW.isoformat())
        events,added=advance(old,doc,NOW)
        self.assertEqual(events[:len(old)],old)
        self.assertEqual(ticket_summary(events,'probability-weekly2')['net'],23000)
        self.assertEqual({e['account'] for e in added if e['type']=='bet'},{ACCOUNT})
        self.assertEqual(len([e for e in events if e['type']=='transition']),1)
        self.assertEqual(paper_report(events,NOW)['accounts']['probability-weekly2']['mode'],'legacy_comparison_settlement_only')

    def test_legacy_early_miss_and_duplicate_revision_rejected(self):
        bet=legacy_bet(); doc=source(count=0)
        doc['recommendation_history']['miss']=dict(bet['legs'][0],result='miss',result_source='official',settled_at=NOW.isoformat())
        events,_=advance([bet],doc,NOW)
        self.assertEqual(ticket_summary(events,bet['account'])['net'],-10000)
        doc['recommendation_history']['duplicate']=copy.deepcopy(doc['recommendation_history']['miss'])
        events,_=advance([bet],doc,NOW)
        self.assertEqual(ticket_summary(events,bet['account'])['settled'],0)

    def test_timestamp_only_republication_and_new_metadata_frozen(self):
        doc=source(count=1); events,_=advance([],doc,NOW)
        leg=next(e for e in events if e['type']=='bet')['legs'][0]
        row=next(iter(doc['recommendation_history'].values()))
        row.update(recorded_at=(NOW+timedelta(hours=5)).isoformat(),published_at=(NOW+timedelta(hours=5)).isoformat())
        resolve(doc,NOW+timedelta(hours=6))
        self.assertIsNotNone(settlement_row(leg,[row],NOW+timedelta(hours=7)))
        for field,value in [('odds',9),('probability',.9),('sel','홈'),('market_label','other'),
                            ('selection_policy','other'),('policy','other')]:
            changed=dict(row); changed[field]=value
            self.assertIsNone(settlement_row(leg,[changed],NOW+timedelta(hours=7)))
        changed=copy.deepcopy(row); changed['tipster_selection']['evidence_hash']='changed'
        self.assertIsNone(settlement_row(leg,[changed],NOW+timedelta(hours=7)))
        self.assertIsNone(settlement_row(leg,[row,row],NOW+timedelta(hours=7)))

    def test_optional_identity_does_not_change_legacy_terms(self):
        leg=legacy_bet()['legs'][0]
        row=dict(leg,selection_policy=POLICY,tipster_selection={'version':POLICY})
        self.assertNotIn('selection_policy',leg)
        self.assertIsNotNone(settlement_row(leg,[row],NOW))

    def test_global_budget_combines_legacy_loss_pending_and_all_leagues(self):
        old=[legacy_bet(index=0),legacy_bet(account='ev_rank-weekly3',index=1)]
        old.append(dict(type='settlement',protocol=LEGACY_PROTOCOL,at=NOW.isoformat(),
                        ticket=old[0]['ticket'],account=old[0]['account'],month='2026-10',net=-10000,payout=0))
        events,_=advance(old,source(count=10),NOW)
        self.assertEqual(ticket_summary(events,ACCOUNT)['bets'],3)
        self.assertEqual(budget_summary(events,'2026-10')['available_loss_budget'],0)
        self.assertEqual(budget_summary(events,'2026-10')['pending_stake'],40000)
        self.assertEqual(events[:len(old)],old)

    def test_migration_does_not_reset_exhausted_legacy_budget(self):
        old=[legacy_bet(account=f'legacy-{i}',index=i) for i in range(6)]
        events,added=advance(old,source(),NOW)
        self.assertFalse(any(e['type']=='bet' for e in added))
        self.assertEqual(budget_summary(events,'2026-10')['available_loss_budget'],-10000)

    def test_pending_prior_month_remains_reserved(self):
        old=[legacy_bet(at=stamp('2026-09-30T10:00:00+09:00'),index=i,account=f'legacy-{i}') for i in range(5)]
        events,added=advance(old,source(),NOW)
        self.assertFalse(any(e['type']=='bet' for e in added))
        self.assertEqual(budget_summary(events,'2026-10')['pending_stake'],50000)

    def test_realized_loss_month_rollover_after_settlement(self):
        doc=source(count=5); events,_=advance([],doc,NOW)
        resolve(doc,NOW+timedelta(hours=6),'miss')
        events,_=advance(events,doc,NOW+timedelta(hours=7))
        self.assertEqual(budget_summary(events,'2026-10')['available_loss_budget'],0)
        now=stamp('2026-11-01T10:01:00+09:00')
        events,_=advance(events,source(now,count=6),now)
        self.assertEqual(ticket_summary(events,ACCOUNT)['bets'],10)
        self.assertEqual(budget_summary(events,'2026-11')['available_loss_budget'],0)

    def test_no_daily_or_weekly_count_limit(self):
        events=[]
        for hour in range(5):
            now=NOW+timedelta(minutes=hour)
            doc=source(now,count=1)
            doc['recommendation_history']['0']['home']=f'fresh-{hour}'
            events,_=advance(events,doc,now)
        self.assertEqual(ticket_summary(events,ACCOUNT)['bets'],5)

    def test_future_only_t30_stale_and_invalid_rows(self):
        changes=[{'kickoff_at':(NOW+timedelta(minutes=30)).isoformat()},
                 {'recorded_at':(NOW+timedelta(seconds=1)).isoformat()},
                 {'published_at':(NOW-timedelta(hours=3)).isoformat(), 'recorded_at':(NOW-timedelta(hours=3)).isoformat()},
                 {'kickoff_at':(NOW-timedelta(minutes=1)).isoformat()},
                 {'kickoff_at':(NOW+timedelta(days=1)).isoformat()},
                 {'selection_policy':'old'}, {'market':'핸디캡'}, {'market_label':'전반'}, {'sel':'홈'},
                 {'result':'hit'}, {'settled_at':NOW.isoformat()}, {'official_market_result':'무'},
                 {'probability':float('nan')}, {'odds':float('inf')}, {'n_way':2}]
        for change in changes:
            with self.subTest(change=change):
                doc=source(count=1); doc['recommendation_history']['0'].update(change)
                events,_=advance([],doc,NOW)
                self.assertFalse(any(e['type']=='bet' for e in events))

    def test_missing_withheld_tied_or_late_vote_blocks(self):
        for change in [{'status':'withheld'}, {'authors':0}, {'evidence_hash':None},
                       {'version':'old'}, {'selected_side':'홈'}, {'vote_shares':{}},
                       {'vote_shares':{'홈':.5,'무':.5}},
                       {'latest_observed_at':(NOW+timedelta(seconds=1)).isoformat()}]:
            with self.subTest(change=change):
                doc=source(count=1); doc['recommendation_history']['0']['tipster_selection'].update(change)
                events,_=advance([],doc,NOW)
                self.assertFalse(any(e['type']=='bet' for e in events))
        doc=source(count=1); doc['recommendation_history']['0'].pop('tipster_selection')
        self.assertFalse(any(e['type']=='bet' for e in advance([],doc,NOW)[0]))

    def test_latest_revision_withheld_cannot_fall_back(self):
        doc=source(count=1)
        newer=copy.deepcopy(doc['recommendation_history']['0'])
        newer.update(id='new',recorded_at=NOW.isoformat())
        newer['tipster_selection']['status']='withheld'
        doc['recommendation_history']['new']=newer
        self.assertFalse(any(e['type']=='bet' for e in advance([],doc,NOW)[0]))
        newer['tipster_selection']['status']='applied'
        bets=[e for e in advance([],doc,NOW)[0] if e['type']=='bet']
        self.assertEqual(bets[0]['legs'][0]['id'],'new')
        newer['recorded_at']=doc['recommendation_history']['0']['recorded_at']
        self.assertFalse(any(e['type']=='bet' for e in advance([],doc,NOW)[0]))

    def test_same_fixture_never_bought_twice_or_retroactively(self):
        doc=source(count=1); events,_=advance([],doc,NOW)
        now=NOW+timedelta(minutes=1)
        doc['generated_at']=now.isoformat()
        row=doc['recommendation_history']['0']; row.update(recorded_at=now.isoformat(),id='new',sel='홈')
        row['tipster_selection'].update(selected_side='홈',vote_shares={'홈':1,'무':0,'원정':0})
        self.assertFalse(any(e['type']=='bet' for e in advance(events,doc,now)[1]))
        resolve(doc,NOW+timedelta(hours=6))
        self.assertFalse(any(e['type']=='bet' for e in advance([],doc,NOW+timedelta(hours=7))[1]))

    def test_legacy_fixture_blocks_new_primary_duplicate(self):
        bet=legacy_bet(at=NOW)
        events,added=advance([bet],source(),NOW)
        self.assertFalse(any(e['type']=='bet' for e in added))

    def test_unavailable_stale_settlement_and_attention(self):
        doc=source(count=1); events,_=advance([],doc,NOW)
        after,_=advance(events,None,NOW+timedelta(minutes=30))
        self.assertEqual(ticket_summary(after,ACCOUNT)['pending_stake'],10000)
        late=NOW+timedelta(days=4)
        after,_=advance(after,doc,late)
        self.assertEqual(sum(e['type']=='attention' for e in after),1)
        self.assertEqual(advance(after,doc,late)[1],[])
        resolve(doc,NOW+timedelta(hours=6))
        after,_=advance(after,doc,late)
        self.assertEqual(ticket_summary(after,ACCOUNT)['settled'],1)

    def test_future_unofficial_and_changed_revision_cannot_settle(self):
        for mode in ('future','unofficial','changed'):
            doc=source(count=1); events,_=advance([],doc,NOW)
            resolve(doc,NOW+timedelta(hours=6))
            row=doc['recommendation_history']['0']
            if mode=='future':row['settled_at']=(NOW+timedelta(days=2)).isoformat()
            if mode=='unofficial':row['result_source']='model'
            if mode=='changed':row['odds']=9
            after,_=advance(events,doc,NOW+timedelta(days=1))
            self.assertEqual(ticket_summary(after,ACCOUNT)['settled'],0)

    def test_bad_protocol_clock_and_doc_fail_closed(self):
        events,_=advance([],source(),NOW)
        with self.assertRaises(ValueError):advance(events,source(),NOW-timedelta(seconds=1))
        events[0]['protocol']='other'
        with self.assertRaises(ValueError):advance(events,source(),NOW)
        with self.assertRaises(ValueError):advance([],{},NOW)

    def test_main_writes_summary_artifacts_without_issue_calls(self):
        import base64
        import scripts.run_forward_paper as runner
        doc=source(); raw=json.dumps(doc).encode()
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'summary.json'; step=Path(folder)/'summary.md'
            env={'GITHUB_REPOSITORY':'test/repo','GH_TOKEN':'unused',
                 'PAPER_SUMMARY_PATH':str(out),'GITHUB_STEP_SUMMARY':str(step)}
            def api(path,method='GET',body=None):
                self.assertNotIn('issues',path)
                if method=='GET':return {'content':base64.b64encode(b'[]').decode(),'sha':'state'}
                self.assertEqual(method,'PUT')
                self.assertEqual(body['branch'],runner.BRANCH)
                return {}
            with patch.dict('os.environ',env), patch.object(runner,'api',side_effect=api), \
                 patch.object(runner,'datetime') as clock, patch.object(runner,'urlopen') as response, patch('builtins.print'):
                clock.now.return_value=NOW
                response.return_value.__enter__.return_value.read.return_value=raw
                runner.main()
            report=json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['primary_account'],ACCOUNT)
            self.assertFalse(report['real_money'])
            self.assertIn('월 손실 5만 원',step.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
