import unittest
from scripts.proto_all_pick_research import parse_row,normalize,choices,historical_summary,summarize


def raw(**kwargs):
    return dict(year=2025,round=2,game_no='001',date_text='01.03(금) 12:00',sport='sc',league='K리그1',
                market_tag='hm',market_label='',market_family='승무패',n_way=3,home='A 2',away='1 B',
                odds='2.0,3.5,3.2',result='홈승',is_void=False,**{}) | kwargs


class AllProtoTests(unittest.TestCase):
    def test_under_is_first_and_over_second(self):
        r=parse_row(raw(market_tag='un',market_label='U 2.5',n_way=2,odds='1.8,1.7',result='오버'),'test')
        a,b=choices(r)
        self.assertEqual((a['selection'],a['net_won']),('언더',-10000))
        self.assertEqual((b['selection'],b['net_won']),('오버',7000))

    def test_wrong_family_label_never_becomes_a_win(self):
        r=parse_row(raw(market_tag='un',market_label='U 2.5',n_way=2,odds='1.8,1.7',result='홈승'),'test')
        self.assertEqual(r['status'],'unrecognized_or_unsettled_result')
        offers,audit,_=normalize([('a',raw(market_tag='un',market_label='U 2.5',n_way=2,odds='1.8,1.7',result='홈승'))])
        self.assertEqual(offers,[])

    def test_three_way_handicap_tie_is_a_middle_win(self):
        r=parse_row(raw(market_tag='hp',market_label='H -1.0',result='핸디무'),'test')
        self.assertEqual([x['hit'] for x in choices(r)],[False,True,False])
        self.assertEqual(choices(r)[1]['selection'],'핸디무')

    def test_five_point_middle_not_missing(self):
        r=parse_row(raw(sport='bk',result='⑤'),'test')
        self.assertEqual(r['family'],'승⑤패')
        self.assertEqual(choices(r)[1]['hit'],True)

    def test_one_run_middle_and_odd_even(self):
        r=parse_row(raw(market_tag='d1',market_label='승①패',sport='bs',result='①'),'test')
        self.assertTrue(choices(r)[1]['hit'])
        r=parse_row(raw(market_tag='',n_way=2,odds='1.74,1.74',result='짝'),'test')
        self.assertEqual(choices(r)[1]['selection'],'짝')

    def test_half_and_full_markets_are_not_combined(self):
        a=raw();b=raw(market_label='h(전반)')
        rows,_,_=normalize([('a',a),('a',b)])
        self.assertEqual(len(rows),2)
        self.assertEqual({r['family'] for r in rows},{'승무패','전반승무패'})

    def test_reissue_price_and_result_conflicts_fail_closed(self):
        for changed in [raw(round=3,odds='2.1,3.5,3.2'),raw(round=3,result='홈패')]:
            rows,audit,_=normalize([('a',raw()),('b',changed)])
            self.assertEqual(rows,[])
            self.assertEqual(audit['market_excluded:conflicting_price_result_or_state_reissues'],1)

    def test_no_fallback_when_reissue_unsettled(self):
        rows,_,_=normalize([('a',raw()),('b',raw(round=3,result='경기전'))])
        self.assertEqual(rows,[])

    def test_identical_reissue_collapses_preserving_aliases(self):
        rows,audit,_=normalize([('a',raw()),('b',raw(round=3))])
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['aliases']),2)
        self.assertEqual(audit['quoted_selections'],3)

    def test_void_sentinel_is_not_a_real_quote(self):
        rows,audit,_=normalize([('a',raw(is_void='True',result='취소',odds='1.0,1.0,1.0'))])
        self.assertEqual(rows,[])
        self.assertEqual(audit['market_excluded:unpriced_void'],1)
        r=parse_row(raw(is_void='True',result='취소'),'test')
        self.assertTrue(all(x['net_won']==0 for x in choices(r)))

    def test_baseball_two_way_draw_refunds_but_three_way_draw_wins(self):
        r=parse_row(raw(sport='bs',n_way=2,odds='1.8,1.8',result='무승부'),'test')
        self.assertEqual(r['status'],'refund')
        self.assertEqual([x['net_won'] for x in choices(r)],[0,0])
        r=parse_row(raw(result='무승부'),'test')
        self.assertEqual([x['hit'] for x in choices(r)],[False,True,False])

    def test_false_string_and_no_overround_quality_cherry_pick(self):
        r=parse_row(raw(is_void='False',odds='1.1,1.1,1.1'),'test')
        self.assertEqual(r['status'],'settled')

    def test_year_boundary_and_decimal_team_scores(self):
        r=parse_row(raw(year=2025,round=1,date_text='12.31(화) 23:00',home='마인츠05 -1.5',away='3 샬케04'),'test')
        self.assertEqual(r['date'],'2024-12-31')
        self.assertEqual((r['home'],r['away']),('마인츠05','샬케04'))

    def test_two_distinct_lines_are_preserved(self):
        rows,_,_=normalize([('a',raw(market_tag='hp',market_label='H -1.0',result='핸디승')),
                           ('b',raw(market_tag='hp',market_label='H -2.0',result='핸디승'))])
        self.assertEqual(len(rows),2)

    def test_screen_does_not_use_evaluation_results(self):
        rows=[]
        for year in [2024,2025]:
            for i in range(100):
                result='홈승' if year==2024 and i<60 else '홈패'
                rows.append(parse_row(raw(year=year,sport='bs',home=f'A{i} 2',n_way=2,odds='2.0,2.0',result=result),'test'))
        report=historical_summary(rows)['train_2024_screen']
        self.assertEqual(len(report['approved_cells']),1)
        self.assertEqual(report['approved_cells'][0]['selection'],'홈')
        self.assertEqual(report['by_year']['2025']['roi'],-1)


if __name__=='__main__':
    unittest.main()
