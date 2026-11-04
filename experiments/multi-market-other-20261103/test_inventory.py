import importlib.util
from pathlib import Path
import unittest
p=Path(__file__).with_name('inventory.py')
s=importlib.util.spec_from_file_location('inventory',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
class InventoryTests(unittest.TestCase):
    def test_year_rollover_and_invalid_date(self):
        self.assertEqual(m.date_of({'year':'2024','round':'1','date_text':'12.31 23:00'}),'2023-12-31T23:00:00')
        self.assertIsNone(m.date_of({'year':'2024','round':'2','date_text':'02.30 12:00'}))
    def test_missing_price_line_void_and_duplicates(self):
        r={'market_label':'U 8.5','result':'언더','odds':'1.8,1.9','n_way':'2','is_void':'False','year':'2024','round':'2','game_no':'1','date_text':'01.02 12:00'}
        bad=dict(r,market_label='', odds='1.0,NaN', is_void='True',result='취소')
        a=m.summarize([r,bad]); self.assertEqual((a['rows'],a['line_present_numeric'],a['valid_price_vector'],a['result_nonvoid_nonempty'],a['void_flag'],a['duplicate_key_rows'],a['pregame_receipt_missing']),(2,1,1,1,1,1,2))
    def test_separate_families(self):
        self.assertNotEqual(m.FAMILIES['승패'],m.FAMILIES['승①패'])
        self.assertNotEqual(m.FAMILIES['승패'],m.FAMILIES['언더오버'])
if __name__=='__main__': unittest.main()
