"""Synthetic contract tests across the actual fact adapter and research queue.

The fixture is synthetic; passing does not authenticate source/receipt or ROI.
"""
import json
from pathlib import Path
import tempfile
import unittest

from src.fact_delta_inputs import to_adapter_input, build_research_envelope
from src.research_dispatch import build_manifest, publish, claim, dispatch_request
from scripts.capture_official_research_inputs import (adapt_with_context,
    run_capture_pipeline, valid_response_bytes)

FIXTURE=Path(__file__).resolve().parents[1]/'experiments/fact-delta-research-adapter-20261031/example-bundle.json'


def adapt(bundle):
    return to_adapter_input(bundle['facts'],bundle['registry'],bundle['baseline'],
                            bundle['market'],input_id='synthetic-integration',workloads=bundle['workloads'])


class FactQueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.bundle=json.loads(FIXTURE.read_text(encoding='utf8'))
        self.temp=tempfile.TemporaryDirectory()

    def tearDown(self): self.temp.cleanup()

    def test_real_adapter_routes_ready_without_changing_baseline(self):
        row=adapt(self.bundle)
        manifest=build_manifest([row])
        self.assertEqual(len(manifest['cases']),1)
        self.assertEqual(len(publish(self.temp.name,manifest)),1)
        self.assertEqual(publish(self.temp.name,manifest),[])
        job=claim(self.temp.name)[0]
        request=dispatch_request(self.temp.name,job)
        self.assertEqual(request['threadId'],'01a18f7e-a7eb-7cb0-af28-2d8de142995b')
        result=build_research_envelope(self.bundle['facts'],self.bundle['registry'],
                                      self.bundle['baseline'],self.bundle['market'],workloads=self.bundle['workloads'])
        self.assertEqual(result['probability_vector'],self.bundle['baseline']['probability_vector'])
        self.assertFalse(result['prediction_changed'])
        self.assertTrue(all(e['external_receipt_verified'] is False for e in row['source_evidence']))

    def test_receipt_only_refresh_no_event_but_price_change_is_new(self):
        publish(self.temp.name,build_manifest([adapt(self.bundle)]),seed=True)
        for f in self.bundle['facts']:
            f['claim']['received_at']='2026-10-31T09:05:00+00:00'
            f['official']['received_at']='2026-10-31T09:05:00+00:00'
            f['market']['received_at']='2026-10-31T09:05:00+00:00'
        self.bundle['market']['received_at']='2026-10-31T09:05:00+00:00'
        self.assertEqual(publish(self.temp.name,build_manifest([adapt(self.bundle)])),[])
        self.bundle['market']['odds']['home']=1.9
        self.bundle['facts'][0]['market']['odds']['home']=1.9
        self.assertEqual(len(publish(self.temp.name,build_manifest([adapt(self.bundle)]))),1)

    def test_probable_and_late_input_cannot_wake_research(self):
        self.bundle['facts'][0]['official']['confirmed']=False
        row=adapt(self.bundle)
        self.assertEqual(row['status'],'rejected')
        self.assertEqual(publish(self.temp.name,build_manifest([row])),[])
        self.bundle=json.loads(FIXTURE.read_text(encoding='utf8'))
        self.bundle['facts'][0]['official']['received_at']='2026-10-31T10:01:00+00:00'
        row=adapt(self.bundle)
        self.assertFalse(row['research_eligible'])
        self.assertEqual(publish(self.temp.name,build_manifest([row])),[])

    def test_collector_adapter_manifest_queue_chain_on_explicit_synthetic_fixture(self):
        root=Path(__file__).resolve().parents[1]
        fixture=json.loads((root/'experiments/verified-fact-capture-20261031/synthetic-real-adapter-fixture.json').read_text(encoding='utf8'))
        self.assertTrue(fixture['synthetic_only'])
        out=Path(self.temp.name)
        result=run_capture_pipeline(fixture['bundle'],
            adapter=lambda facts:adapt_with_context(facts,to_adapter_input=to_adapter_input,context=fixture['context']),
            build_manifest=build_manifest,raw_path=out/'raw.json',inputs_path=out/'adapter.json',manifest_path=out/'manifest.json')
        self.assertEqual(result['inputs'],1)
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf8'))
        self.assertEqual(len(publish(out/'queue',manifest)),1)
        self.assertEqual(publish(out/'queue',manifest),[])
        self.assertEqual(dispatch_request(out/'queue',claim(out/'queue')[0])['threadId'],
                         '01a18f7e-a7eb-7cb0-af28-2d8de142995b')

    def test_real_captured_bytes_are_recomputable_but_probable_is_not_confirmed(self):
        root=Path(__file__).resolve().parents[1]
        actual=json.loads((root/'experiments/verified-fact-capture-20261031/capture-with-bytes.json').read_text(encoding='utf8'))
        self.assertEqual(len(actual['requests']),2)
        self.assertTrue(all(valid_response_bytes(e) for e in actual['requests']))
        self.assertEqual(len(actual['facts']),2)
        self.assertTrue(all(f['official']['confirmed'] is False for f in actual['facts']))
        previous=json.loads((root/'experiments/verified-fact-capture-20261031/capture.json').read_text(encoding='utf8'))
        self.assertEqual({f['semantic_sha256'] for f in previous['facts']},
                         {f['semantic_sha256'] for f in actual['facts']})


if __name__=='__main__': unittest.main()
