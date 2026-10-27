import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_tipster_sources import replay, comparison, content_overlap, join, source_tip


def row(day, ident, winner=0, odds=(2.,2.)):
    return dict(date=day,kickoff=day+'T19:00:00+09:00',sport='bk',home=str(ident),away='away',
                odds=list(odds),winner=winner,picks={'tipster':1,'market90_tipster10':0})


def test_reserves_same_day_maximum_loss_and_resets_month():
    rows=[row('2024-01-01',i) for i in range(10)]+[row('2024-01-02',11),row('2024-02-01',12)]
    r=replay(rows,'tipster')
    assert r['bets']==6
    assert r['monthly_net']=={'2024-01':-50000,'2024-02':-10000}
    assert r['max_daily_drawdown_krw']==60000


def test_profit_can_fund_later_days_not_same_day():
    rows=[row('2024-01-01',i) for i in range(10)]+[row('2024-01-02',i+10) for i in range(10)]
    r=replay(rows,'market90_tipster10')
    assert r['bets']==15
    assert r['profit_krw']==150000
    assert r['roi_pct']==100


def test_uncapped_same_fixture_exposure():
    rows=[row('2024-01-01',i) for i in range(10)]
    c=comparison(rows)
    assert c['uncapped']['tipster']['bets']==c['uncapped']['market90_tipster10']['bets']==10
    assert c['capped']['tipster']['profit_krw']==-50000
    assert c['descriptive_monthly_mean_difference_95_interval'] is None


def test_order_independence_and_money_rounding():
    rows=[row('2024-01-01',i,odds=(1.93,1.62)) for i in range(9)]
    assert replay(rows,'market90_tipster10')==replay(rows[::-1],'market90_tipster10')
    assert replay(rows,'market90_tipster10')['profit_krw']==46500


def test_copy_warning_requires_other_site_and_body_overlap():
    body='선발투수와최근전력을확인한분석문입니다.'*20
    a={'url':'https://other.test/1','body':body,'title':'[KBL] 2025년 1월 1일 경기'}
    b={'url':'https://first.test/1','body':body,'title':a['title']}
    ledger=[{'site':'other.test','date':'2025-01-01','article_url':a['url']}]
    found=content_overlap(ledger,{a['url']:a},[a,b])
    assert len(found)==1
    assert found[0]['similar_to']==b['url']
    assert found[0]['character_12gram_jaccard']==1


def test_regular_kovo_invalid_row_quarantined_not_fabricated(monkeypatch):
    import collect_official_korean as c
    class Fetch:
        def get(self,url,params,headers):
            if url.endswith('season-list'):
                return {'payload':[{'ryear':'2023-2024','seasonCode':'020','leagues':[{'leagueCode':'201'}]}]},url
            if url.endswith('game-schedule'):
                return {'payload':{'content':[{'gnum':234}], 'page':{'totalPages':1,'totalElements':1}}},url
            return [{'gmkey':'real'}],url
    def bad(*args):
        raise ValueError('Inconsistent KOVO set score')
    monkeypatch.setattr(c,'normalize_kovo',bad)
    monkeypatch.setattr(c,'normalize_kbl',lambda *args:dict(official_id='real',league='KBL',date='2024-01-01',home='A',away='B',kickoff='2024-01-01T19:00:00+09:00'))
    rows,counts,rejected=c.collect(Fetch(),2024)
    assert len(rows)==1
    assert counts['KOVO invalid score/time or unmapped team']==4
    assert all(x['raw']=={'gnum':234} for x in rejected)


def test_join_rejects_forged_body_hash_before_result_matching():
    import pandas as pd
    a={'url':'https://other.test/1','body':'승무패: 서울삼성 승','full_article':True,'body_sha256':'wrong'}
    ledger,rejected=join([a],[],pd.DataFrame(),{},set())
    assert ledger==[]
    assert rejected[0]['reason']=='missing_full_article_or_hash_mismatch'


def test_monthly_results_not_equated_with_drawdown():
    r1=row('2024-01-01',1,odds=(1.7,2.))
    rows=[r1]+[row('2024-01-02',i,winner=1) for i in range(5)]
    r=replay(rows,'market90_tipster10')
    assert r['monthly_net']=={'2024-01':-43000}
    assert r['max_daily_drawdown_krw']==50000


def test_native_pick_requires_unique_team_and_explicit_moneyline():
    r={'raw_team_names':{'home':'Arizona Diamondbacks','away':'Chicago Cubs'}}
    def article(text):
        return dict(url='https://pickdawgz.com/example',native_pick=text,body='Article '+text)
    assert source_tip(article("Chris Ruffolo's Pick: Arizona -125"),r,{})==0
    assert source_tip(article("Chris Ruffolo's Pick: Chicago Cubs"),r,{})==1
    assert source_tip(article("Chris Ruffolo's Pick: Chicago +1.5"),r,{}) is None
    assert source_tip(article("Chris Ruffolo's Pick: Over 8.5"),r,{}) is None
    r['raw_team_names']['home']='Chicago White Sox'
    assert source_tip(article("Chris Ruffolo's Pick: Chicago -125"),r,{}) is None
