import unittest
from scripts.proto_saved_offer_profitability import latest_snapshots,result_index,joined_rosters,compare,vector


def snap(hash='h',capture='2026-08-30T09:00:00+09:00',count=2):
    return dict(record_hash=hash,event_id='e',kickoff='2026-08-30T10:00:00+09:00',
                captured_at=capture,as_of=capture,market_observed_at=capture,saved_options_count=count,
                score_forecast={'contract':{'score_unit':'runs','total_market_unit':'runs'}})


def archive(**kw):
    return dict(year=2026,round=100,game_no='001',date_text='08.30(일) 10:00',sport='bs',league='KBO',
                market_tag='hm',market_label='',market_family='승패',n_way=2,home='A 2',away='1 B',
                odds='1.60,2.00',result='홈승',is_void=False)|kw


def options(hash='h',score=True):
    result=[]
    for i,(side,price) in enumerate([('홈',2.1),('원정',1.8)]):
        result.append(dict(record_hash=hash,event_id='e',kickoff='2026-08-30T10:00:00+09:00',
            identity_complete=True,identity_join={'fixture':{'year':2026,'round':100,'home':'A','away':'B','league':'KBO','sport':'bs'},
                'game_no':'0001','n_way':2,'identity_recomputation_pass':True},market='승패',label='',line=None,
            selection=side,odds=price,market_probability=[.55,.45][i],final_probability=[.55,.45][i],
            score_model_probability=[.7,.3][i] if score else None,score_probability_usable_for_model_comparison=score,
            selected_in_snapshot=i==0,decision_action='market_reference'))
    return result


def index(raw=None):
    return result_index([('test',archive() if raw is None else raw)])[0]


class SavedAllOfferTests(unittest.TestCase):
    def test_cutoff_latest_selected_before_option_eligibility(self):
        a=snap();b=snap('late','2026-08-30T09:40:00+09:00')
        result,_=latest_snapshots([a,b],30)
        self.assertEqual(set(result),{'h'})
        result,_=latest_snapshots([a,b],0)
        self.assertEqual(set(result),{'late'})

    def test_latest_empty_is_not_backfilled(self):
        a=snap();b=snap('new','2026-08-30T09:20:00+09:00',0)
        rows,audit=joined_rosters([a,b],options(),index())
        self.assertEqual(rows,[])
        self.assertEqual(audit['latest_empty_roster_events'],1)

    def test_latest_missing_score_is_not_backfilled(self):
        a=snap();b=snap('new','2026-08-30T09:20:00+09:00')
        rows,_=joined_rosters([a,b],options()+options('new',False),index())
        report=compare(rows)
        self.assertEqual(report['same_score_rosters'],{})
        self.assertNotIn('saved_score_shadow',report['policies'])

    def test_snapshot_price_not_closing_archive_price_is_paid(self):
        rows,_=joined_rosters([snap()],options(),index())
        self.assertEqual(rows[0][0]['net_won'],11000)
        self.assertEqual(rows[0][1]['net_won'],-10000)

    def test_different_line_result_cannot_cross_join(self):
        raw=archive(market_tag='hp',market_label='H -1.5',result='핸디승')
        opts=options()
        for r,side in zip(opts,['핸디홈','핸디원정']):r.update(market='핸디캡',line=-2.5,label='H -2.5',selection=side)
        rows,audit=joined_rosters([snap()],opts,index(raw))
        self.assertEqual(rows,[])
        self.assertEqual(audit['option_no_exact_product_result'],2)

    def test_incomplete_roster_does_not_borrow_other_snapshot(self):
        rows,audit=joined_rosters([snap()],options()[:1],index())
        self.assertEqual(rows,[])
        self.assertEqual(audit['incomplete_or_duplicate_market_rosters'],1)

    def test_cancelled_product_can_settle_original_saved_price_as_refund(self):
        raw=archive(result='취소',is_void=True,odds='1.00,1.00')
        rows,_=joined_rosters([snap()],options(),index(raw))
        self.assertEqual([r['net_won'] for r in rows[0]],[0,0])

    def test_conflicting_results_are_never_selected_by_win(self):
        data,audit=result_index([('a',archive()),('b',archive(result='홈패'))])
        self.assertEqual(data,{})
        self.assertEqual(audit['conflicting_issued_results'],1)

    def test_withheld_choices_still_researchable_but_not_actual_selected(self):
        opts=options()
        for r in opts:r['decision_action']='withhold'
        rows,_=joined_rosters([snap()],opts,index())
        report=compare(rows)
        self.assertEqual(report['policies']['market']['n'],1)
        self.assertNotIn('actually_selected_in_saved_snapshot',report['policies'])

    def test_half_and_odd_even_never_receive_full_score_model(self):
        r=dict(score_probability_usable_for_model_comparison=True,score_model_probability=.5,
               family='전반승패',sport='bs',score_contract={'score_unit':'runs','total_market_unit':'runs'})
        self.assertIsNone(vector([r,r],'score_model_probability',True))
        r['family']='홀짝'
        self.assertIsNone(vector([r,r],'score_model_probability',True))

    def test_volleyball_total_points_not_scored_from_sets(self):
        r=dict(score_probability_usable_for_model_comparison=True,score_model_probability=.5,
               family='언더오버',sport='vl',score_contract={'score_unit':'sets','total_market_unit':'points'})
        self.assertIsNone(vector([r,r],'score_model_probability',True))

    def test_future_asof_and_naive_time_fail(self):
        a=snap();a['as_of']='2026-08-30T09:10:00+09:00'
        b=snap('new');b['captured_at']='2026-08-30T09:00:00'
        result,audit=latest_snapshots([a,b])
        self.assertEqual(result,{})
        self.assertEqual(audit['snapshot_time_excluded'],1)
        self.assertEqual(audit['snapshot_invalid_time'],1)


if __name__=='__main__':unittest.main()
