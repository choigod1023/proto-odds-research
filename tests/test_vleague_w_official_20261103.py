import copy
import gzip
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'experiments'/'vleague-w-official-20261103'
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0,str(ROOT)); spec.loader.exec_module(m)
    return m
collect=module('collect')
audit=module('audit')

class OfficialCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=collect.census()
        cls.events=collect.load(collect.MANIFEST)['games']
    def test_every_fixture_raw_hash_identity_and_set_one(self):
        self.assertEqual(len(self.fixtures),126)
        self.assertEqual(len(self.events),126)
        for game in self.fixtures:
            e=self.events[str(game['number'])]; raw=(ROOT/e['file']).read_bytes()
            self.assertEqual(collect.sha(raw),e['sha256'])
            self.assertEqual(collect.validate(raw,game)['lineup']['rset'],1)
            self.assertEqual(e['availability'],'retrospective_postgame_pit_unverified')
    def test_actual_source_foreign_rows_are_excluded_and_future_poison_does_not_change_lineup(self):
        game=self.fixtures[0];p=collect.validate((ROOT/self.events[str(game['number'])]['file']).read_bytes(),game)
        self.assertTrue(any(x.get('gpart')!='201' for x in p['player']))
        before=audit.lineup_rows(p,game);self.assertEqual(len([r for r in before if r['slot']<=6 and r['player_id']]),12)
        poison=copy.deepcopy(p);poison['recentGames']=[{'gdate':'2100-01-01','result':'fake'}];poison['game']['homeSetPoint']=999
        for x in poison['player']:x['point']=999999
        self.assertEqual(before,audit.lineup_rows(poison,game))
    def test_duplicate_name_ambiguous_player_is_not_silently_accepted(self):
        game=self.fixtures[0];p=collect.validate((ROOT/self.events[str(game['number'])]['file']).read_bytes(),game)
        start=audit.lineup_rows(p,game)[0]; candidate=next(x for x in p['player'] if x.get('pcode')==start['player_id'] and x.get('gpart')=='201')
        clone=copy.deepcopy(candidate);clone['pcode']='fake-conflict';p['player'].append(clone)
        row=audit.lineup_rows(p,game)[0];self.assertIsNone(row['player_id']);self.assertEqual(row['identity_match_count'],2)
    def test_wrong_team_and_wrong_set_rejected(self):
        game=self.fixtures[0];p=json.loads((ROOT/self.events[str(game['number'])]['file']).read_bytes());p['payload']['game']['homeCode']='9999'
        with self.assertRaises(AssertionError):collect.validate(json.dumps(p).encode(),game)
        p=json.loads((ROOT/self.events[str(game['number'])]['file']).read_bytes());p['payload']['lineup']['rset']=2
        with self.assertRaises(AssertionError):collect.validate(json.dumps(p).encode(),game)
    def test_volleyball_set_thresholds_and_deuce(self):
        for a,b,n in [(25,0,1),(25,23,4),(26,24,3),(15,13,5),(17,15,5)]:
            self.assertTrue(audit.valid_set_score(a,b,n),(a,b,n))
        for a,b,n in [(30,20,1),(25,24,1),(14,12,5),(16,13,5),(0,0,1)]:
            self.assertFalse(audit.valid_set_score(a,b,n),(a,b,n))
    def test_discovery_bodies_are_exact_and_have_actual_receipt(self):
        folder=ROOT/'evidence'/'postgame'
        for e in json.loads((folder/'discovery.json').read_text(encoding='utf8')):
            body=(folder/(e['name']+'.httpbody')).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(),e['sha256'])
            self.assertEqual(len(body),e['length'])
            self.assertTrue(e['received_at'].endswith('+00:00'))
    def test_saved_price_body_hashes_and_no_pit_claim(self):
        d=json.loads((ROOT/'price-audit.json').read_text(encoding='utf8'))
        self.assertEqual(d['validated_full_market_selection_count'],0);self.assertEqual(d['strict_pit_count'],0)
        for e in d['source_manifest']:
            b=gzip.decompress((ROOT/e['file']).read_bytes());self.assertEqual(hashlib.sha256(b).hexdigest(),e['body_sha256'])

if __name__=='__main__':unittest.main()
