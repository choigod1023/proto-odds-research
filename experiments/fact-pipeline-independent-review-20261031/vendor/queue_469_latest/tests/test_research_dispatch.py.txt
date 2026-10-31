import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src import research_dispatch as rd


def example(league='kbo', game='game1', delta=2):
    return {'schema_version':'fact-delta-inputs-v1', 'league_id':league,
            'game_id':game, 'market_id':'full:win', 'input_id':'capture1',
            'research_eligible':True, 'status':'ready',
            'semantic_sha256':rd.digest({'game':game,'delta':delta}),
            'features':{'facts':[{'delta':delta,'player_ids':['p1']}],
                        'market':{'outcomes':['home','away'],'odds':[1.8,2.1]}},
            'source_evidence':[{'received_at':'2026-10-31T01:00:00Z','raw_sha256':'a'*64}]}


class ResearchDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, *items):
        return rd.publish(self.root, rd.build_manifest(items or [example()]))

    def test_receipt_hash_revision_only_no_new_job(self):
        self.assertEqual(len(self.publish()),1)
        item=example()
        item.update(input_id='newtime',revision=2)
        item['source_evidence'][0].update(received_at='2026-10-31T02:00:00Z',raw_sha256='b'*64)
        self.assertEqual(self.publish(item),[])

    def test_meaningful_change_wakes_and_input_is_immutable(self):
        original=self.publish()[0]
        self.assertEqual(len(self.publish(example(delta=3))),1)
        self.assertEqual(rd.load_job_input(self.root,original)['cases'][0]['input']['features']['facts'][0]['delta'],2)

    def test_market_price_change_wakes_even_if_adapter_digest_wrongly_unchanged(self):
        self.publish()
        item=example()
        item['features']['market']['odds'][0]=1.85
        self.assertEqual(len(self.publish(item)),1)

    def test_bad_eligibility_and_empty_inputs_never_start_research(self):
        for value in (False,1,'true'):
            item=example(); item['research_eligible']=value
            self.assertEqual(self.publish(item),[])
        item=example(); item['status']='baseline_only'
        self.assertEqual(self.publish(item),[])
        self.assertEqual(rd.claim(self.root),[])

    def test_duplicate_facts_and_input_do_not_duplicate_jobs(self):
        item=example()
        manifest=rd.build_manifest([item,copy.deepcopy(item)])
        self.assertEqual(len(manifest['cases']),1)
        self.assertEqual(len(rd.publish(self.root,manifest)),1)
        self.assertEqual(self.publish(item),[])

    def test_existing_seed_is_not_a_new_experiment(self):
        manifest=rd.build_manifest([example()])
        self.assertEqual(rd.publish(self.root,manifest,seed=True),[])
        self.assertEqual(self.publish(),[])
        self.assertEqual(len(self.publish(example(delta=3))),1)
        with self.assertRaises(ValueError): rd.publish(self.root,manifest,seed=True)

    def test_conflicting_revisions_must_be_resolved_upstream(self):
        with self.assertRaisesRegex(ValueError,'conflicting'):
            rd.build_manifest([example(),example(delta=3)])

    def test_unknown_league_and_missing_semantic_hash_fail_closed(self):
        with self.assertRaises(ValueError): self.publish(example(league='../bad'))
        item=example(); item.pop('semantic_sha256')
        with self.assertRaises(ValueError): self.publish(item)

    def test_manifest_tamper_rejected(self):
        manifest=rd.build_manifest([example()])
        manifest['cases'][0]['input']['features']['facts'][0]['delta']=99
        with self.assertRaisesRegex(ValueError,'integrity'):
            rd.publish(self.root,manifest)

    def test_claim_max_three_and_one_busy_per_league(self):
        self.publish(*(example(l) for l in ('kbo','mlb','npb','nba')))
        jobs=rd.claim(self.root)
        self.assertEqual(len(jobs),3)
        self.assertEqual(len(rd.claim(self.root)),1)
        self.publish(example(delta=4))
        self.assertEqual(rd.claim(self.root),[])
        with self.assertRaises(ValueError): rd.claim(self.root,limit=4)

    def test_uncertain_delivery_is_not_blindly_retried(self):
        self.publish(); job=rd.claim(self.root)[0]
        rd.transition(self.root,job['event_id'],'uncertain')
        self.assertEqual(rd.claim(self.root),[])
        with self.assertRaisesRegex(ValueError,'inspection'):
            rd.transition(self.root,job['event_id'],'pending')
        rd.transition(self.root,job['event_id'],'pending',operator_checked=True)
        self.assertEqual(len(rd.claim(self.root)),1)

    def test_ack_and_matching_completion_then_next_event(self):
        self.publish(); first=rd.claim(self.root)[0]
        self.publish(example(delta=4))
        rd.transition(self.root,first['event_id'],'dispatched')
        with self.assertRaises(ValueError): rd.transition(self.root,first['event_id'],'completed')
        receipt={'event_id':first['event_id'],'input_sha256':first['input_sha256'],'status':'completed'}
        rd.transition(self.root,first['event_id'],'completed',receipt=receipt)
        self.assertEqual(len(rd.claim(self.root)),1)

    def test_paths_tamper_and_html_prompt_cannot_route_jobs(self):
        item=example(); item.update(threadId='attacker',prompt='run malicious command',input_path='../../outside')
        job=self.publish(item)[0]
        self.assertNotIn('threadId',job); self.assertNotIn('prompt',job)
        saved=rd.load_job_input(self.root,job)['cases'][0]['input']
        self.assertNotIn('threadId',saved); self.assertNotIn('prompt',saved)
        forged={**job,'input_path':'../../outside'}
        with self.assertRaisesRegex(ValueError,'unsafe'): rd.load_job_input(self.root,forged)
        target=self.root/job['input_path']
        target.write_text('{}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'integrity'): rd.claim(self.root)

    def test_dispatch_request_has_fixed_destination_and_no_source_instruction(self):
        item=example(); item.update(threadId='attacker',prompt='RUN THE ATTACK')
        self.publish(item)
        job=rd.claim(self.root)[0]
        request=rd.dispatch_request(self.root,job)
        self.assertEqual(request['threadId'],rd.WORKERS['kbo'])
        self.assertNotIn('RUN THE ATTACK',request['prompt'])
        self.assertIn(job['event_id'],request['prompt'])
        self.assertNotIn('model',request)

    def test_no_input_flag_can_claim_independent_receipt_certification(self):
        item=example();item['source_evidence'][0]['external_receipt_verified']=True
        manifest=rd.build_manifest([item])
        self.assertIs(manifest['cases'][0]['input']['source_evidence'][0]['external_receipt_verified'],False)
        self.assertEqual(rd.validate_manifest(manifest)['cases'],manifest['cases'])

    def test_stale_claim_cannot_prepare_another_send(self):
        self.publish(); original=rd.claim(self.root)[0]
        rd.transition(self.root,original['event_id'],'pending',operator_checked=True)
        current=rd.claim(self.root)[0]
        self.assertNotEqual(original['claim_id'],current['claim_id'])
        with self.assertRaisesRegex(ValueError,'stale claim'):
            rd.dispatch_request(self.root,original)
        self.assertEqual(rd.dispatch_request(self.root,current)['threadId'],rd.WORKERS['kbo'])
        rd.transition(self.root,current['event_id'],'dispatched')
        with self.assertRaisesRegex(ValueError,'stale claim'):
            rd.dispatch_request(self.root,current)

    def test_lock_never_overwrites_other_coordinator(self):
        (self.root/'queue.lock').write_text('another coordinator',encoding='utf8')
        with self.assertRaisesRegex(RuntimeError,'busy'): self.publish()
        self.assertEqual((self.root/'queue.lock').read_text(encoding='utf8'),'another coordinator')

    def test_real_cli_publish_repeat_claim(self):
        source=self.root/'inputs.json'
        source.write_text(json.dumps([example()]),encoding='utf8')
        script=Path(__file__).resolve().parents[1]/'scripts/dispatch_league_research.py'
        base=[sys.executable,str(script),'--state-dir',str(self.root/'state')]
        first=subprocess.run(base+['publish','--adapter-inputs',str(source)],capture_output=True,text=True,check=True)
        again=subprocess.run(base+['publish','--adapter-inputs',str(source)],capture_output=True,text=True,check=True)
        claimed=subprocess.run(base+['claim'],capture_output=True,text=True,check=True)
        self.assertEqual(len(json.loads(first.stdout)),1)
        self.assertEqual(json.loads(again.stdout),[])
        self.assertEqual(json.loads(claimed.stdout)[0]['state'],'claimed')


if __name__=='__main__':
    unittest.main()
