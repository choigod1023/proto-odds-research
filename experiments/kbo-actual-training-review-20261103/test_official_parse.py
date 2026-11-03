import hashlib
import json
import pathlib
import unittest

from official_parse import confirmed_lineups, explicit_starting_pitchers

LINEUP = pathlib.Path(r'C:\Users\user\Documents\Codex\klu\.research-state\kbo-lineups-v4\raw\f976fdd31c35c9fbadbd4eeccc8873307f78241eb9440bbca51723bf471179b2')
BOX = pathlib.Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-review-20261103\experiments\kbo-player-independent-review-20261103\kbo-pitcher-source-check\20230401HHWO0.boxscore.raw')

@unittest.skipUnless(LINEUP.exists() and BOX.exists(), 'pinned historical samples unavailable')
class OfficialExample(unittest.TestCase):
    def setUp(self):
        self.lineup = LINEUP.read_bytes()
        self.box = BOX.read_bytes()
        self.assertEqual(hashlib.sha256(self.lineup).hexdigest(), 'f976fdd31c35c9fbadbd4eeccc8873307f78241eb9440bbca51723bf471179b2')
        self.assertEqual(hashlib.sha256(self.box).hexdigest(), 'aeeeffecb2bcbca68e06edfbeb55f3629a0c5c39879abe69946a6c707f3eaefe')

    def test_confirmed_lineup_and_fixture_mismatch(self):
        result = confirmed_lineups(self.lineup, '20230401HHWO0', 'WO', 'HH', 2023)
        self.assertEqual(result['confirmed_lineups']['WO'][0], (1, '이용규'))
        self.assertEqual(result['confirmed_lineups']['HH'][0], (1, '노수광'))
        with self.assertRaises(ValueError):
            confirmed_lineups(self.lineup, '20230401HHWO1', 'WO', 'HH', 2023)
        with self.assertRaises(ValueError):
            confirmed_lineups(self.lineup, '20230401HHWO0', 'HH', 'WO', 2023)
        data = json.loads(self.lineup)
        data[0][0]['LINEUP_CK'] = False
        with self.assertRaises(ValueError):
            confirmed_lineups(json.dumps(data).encode(), '20230401HHWO0', 'WO', 'HH', 2023)

    def test_explicit_starters_are_not_winning_or_losing_pitchers(self):
        result = explicit_starting_pitchers(self.box, requested_game_id='20230401HHWO0', away='HH', home='WO')
        self.assertEqual(result['starter_names'], {'HH': '스미스', 'WO': '안우진'})
        self.assertNotIn('문성현', result['starter_names'].values())
        self.assertNotIn('장시환', result['starter_names'].values())
        with self.assertRaises(ValueError):
            explicit_starting_pitchers(self.box, requested_game_id='', away='HH', home='WO')

class DuplicateNameParsing(unittest.TestCase):
    def test_duplicate_names_require_separate_identity_resolution(self):
        meta=lambda team: [{'G_ID':'g','T_ID':team,'SEASON_ID':2025,'SR_ID':0}]
        table=lambda names: [json.dumps({'rows':[{'row':[{'Text':str(i+1)},{'Text':'position'},{'Text':name}]} for i,name in enumerate(names)]})]
        same=['동명','동명']+[str(i) for i in range(7)]
        other=[str(i) for i in range(9)]
        body=json.dumps([[{'LINEUP_CK':True}],meta('H'),meta('A'),table(same),table(other)]).encode()
        with self.assertRaises(ValueError):
            confirmed_lineups(body,'g','H','A',2025)
        parsed=confirmed_lineups(body,'g','H','A',2025,allow_duplicate_names=True)
        self.assertEqual(parsed['confirmed_lineups']['H'][:2],[(1,'동명'),(2,'동명')])
