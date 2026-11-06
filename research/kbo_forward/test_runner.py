import copy,json,sys
from datetime import timedelta
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent))
import runner, ledger
from test_ledger import payload, old

class Official:
    count=0
    def __init__(self,x):
        self.x=x;self.snapshot_calls=0
        e=x['event'];dt=ledger.moment(e['kickoff_at']).astimezone(runner.KST)
        self.game={'source':{'G_ID':e['official_game_id'],'G_DT':dt.strftime('%Y%m%d'),'G_TM':dt.strftime('%H:%M'),'SEASON_ID':str(e['season']),'HOME_ID':e['home_id'],'AWAY_ID':e['away_id'],'HOME_NM':'HOME','AWAY_NM':'AWAY','GAME_STATE_SC':'1','CANCEL_SC_ID':'0'}}
    def schedule(self,dates):return {'games':[self.game]}
    def snapshot(self,game):
        self.snapshot_calls+=1
        x=copy.deepcopy(self.x);x.pop('market');x['evidence'].pop('odds');x['row']['history_coverage']={k:{'status':'complete'} for k in ('home','away')}
        return {'source_verified':True,'payload':x}
    def result(self,game):return {'verified':False}

def quote(x,state,now):
    import prices
    from test_prices import ROW,ROUND,detail,ms
    target=Path(state)/'prices/price-evidence';target.mkdir(parents=True,exist_ok=True)
    start=ledger.moment(x['event']['kickoff_at'])
    row=ROW|{'homeId':x['event']['home_id'],'awayId':x['event']['away_id'],'gameDate':ms(start),'endDate':ms(start),'winAllot':1.80,'loseAllot':1.90}
    r=ROUND|{'saleEndDate':ms(start+timedelta(hours=4))}
    body=detail([row]);listing=json.dumps({'protoGames':[r],'test_only':True}).encode()
    for raw in (body,listing):(target/(ledger.digest(raw)+'.body')).write_bytes(raw)
    evidence={'uri':prices.DETAIL_URL,'body_file':str(target/(ledger.digest(body)+'.body')),'sha256':ledger.digest(body),'received_at':now.isoformat(),'round_listing_sha256':ledger.digest(listing)}
    q,_=prices.parse_detail(body,r,[x['event']],now.isoformat(),evidence)
    assert len(q)==1
    return q[0]

def test_actual_runner_persists_paired_capture_and_restart_is_idempotent(tmp_path):
    x=payload('full_game_2way');x['event'].update(home_id='SS',away_id='HT');x['row'].update(home_id='SS',away_id='HT');now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2)
    x["evidence"]["odds"]["received_at"]=now.isoformat();official=Official(x);prices=lambda *a:{'quotes':[quote(x,tmp_path,now)],'excluded':[],'requests':[]}
    a=runner.run_once(tmp_path,official,prices,clock=lambda:now,infer=old.fake_infer)
    before=(tmp_path/'paired-ledger.jsonl').read_bytes()
    b=runner.run_once(tmp_path,official,prices,clock=lambda:now+timedelta(minutes=5),infer=old.fake_infer)
    assert a['captured_this_run']==1 and b['captured_this_run']==0
    assert (tmp_path/'paired-ledger.jsonl').read_bytes()==before and official.snapshot_calls==1
    assert json.loads((tmp_path/'summary.json').read_bytes())['captured']==1

def test_price_unavailable_records_forecast_but_never_roi(tmp_path):
    x=payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2);o=Official(x)
    prices=lambda *a:{'quotes':[],'excluded':[{'reason':'official_html_error'}],'requests':[]}
    a=runner.run_once(tmp_path,o,prices,clock=lambda:now,infer=old.fake_infer)
    b=runner.run_once(tmp_path,o,prices,clock=lambda:now+timedelta(minutes=5),infer=old.fake_infer)
    assert a['unpriced_this_run']==1 and b['unpriced_this_run']==0 and o.snapshot_calls==1
    assert b['comparison']['captured']==0 and not b['comparison']['groups']


def test_opt_in_keeps_raw_lineup_requests_after_first_forecast(tmp_path):
    x = payload()
    now = ledger.moment(x['event']['kickoff_at']) - timedelta(hours=2)

    class Observer(Official):
        def __init__(self, packet):
            super().__init__(packet)
            self.lineup_calls = []

        def schedule(self, dates):
            self.dates = dates
            return super().schedule(dates)

        def fetch_lineup(self, game):
            self.lineup_calls.append(game)

    observer = Observer(x)
    empty_prices = lambda *a: {'quotes': [], 'excluded': [], 'requests': []}
    runner.run_once(tmp_path, observer, empty_prices, clock=lambda: now, infer=old.fake_infer)
    path = tmp_path / 'paired-ledger.jsonl'
    first_forecast = path.read_bytes()
    runner.run_once(tmp_path, observer, empty_prices, clock=lambda: now + timedelta(minutes=5),
                    infer=old.fake_infer, watch_lineup_revisions=True)
    assert observer.lineup_calls == [observer.game['source']]
    assert observer.snapshot_calls == 1
    assert len(observer.dates) == 3
    assert path.read_bytes() == first_forecast


def test_postdeadline_run_does_not_backfill_or_fetch_prices(tmp_path):
    x=payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(minutes=29);o=Official(x)
    def forbidden(*a):raise AssertionError('unnecessary price request')
    d=runner.run_once(tmp_path,o,forbidden,clock=lambda:now,infer=old.fake_infer)
    assert d['status']=='ok' and o.snapshot_calls==0 and d['comparison']['captured']==0
    assert not ledger.read(tmp_path/'paired-ledger.jsonl')[0]['observed_before_cutoff']

def test_process_scoped_lock_blocks_another_writer_and_releases(tmp_path):
    from locking import single_writer
    p=tmp_path/'x.lock'
    with single_writer(p) as first:
        assert first
        with single_writer(p) as second:assert not second
    with single_writer(p) as third:assert third

def test_more_than_five_unconfirmed_candidates_rotate_on_next_run(tmp_path):
    x=payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2)
    class Many(Official):
        def __init__(self,x):super().__init__(x);self.seen=[]
        def schedule(self,dates):
            games=[]
            for i in range(8):
                g=copy.deepcopy(self.game);g['source']['G_ID']=f'candidate-{i}';games.append(g)
            return {'games':games}
        def snapshot(self,g):
            self.seen.append(g['source']['G_ID']);return {'source_verified':False,'missing':['unannounced']}
    o=Many(x);prices=lambda *a:{'quotes':[],'excluded':[],'requests':[]}
    runner.run_once(tmp_path,o,prices,clock=lambda:now,infer=old.fake_infer)
    assert len(o.seen)==5
    runner.run_once(tmp_path,o,prices,clock=lambda:now+timedelta(minutes=5),infer=old.fake_infer)
    assert len(set(o.seen))==8

def test_os_lock_recovers_after_process_termination(tmp_path):
    import subprocess
    from locking import single_writer
    path=tmp_path/'killed.lock'
    source='import sys,time;sys.path.insert(0,sys.argv[1]);from locking import single_writer\nwith single_writer(sys.argv[2]) as owned:\n print(owned,flush=True)\n time.sleep(30)'
    # A Windows venv launcher can leave its real Python child holding the lock
    # when only the launcher is terminated. This probe needs no venv packages.
    executable=getattr(sys,'_base_executable',sys.executable) if sys.platform=='win32' else sys.executable
    proc=subprocess.Popen([executable,'-B','-c',source,str(Path(__file__).parent),str(path)],stdout=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='True'
        with single_writer(path) as owned:assert not owned
    finally:
        proc.terminate();proc.wait(timeout=5);proc.stdout.close()
    with single_writer(path) as recovered:assert recovered


def test_mutated_price_sale_close_and_product_label_rejected(tmp_path):
    x=payload('full_game_2way');x['event'].update(home_id='SS',away_id='HT');x['row'].update(home_id='SS',away_id='HT')
    now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2);q=quote(x,tmp_path,now)
    q['close_at']=(now+timedelta(days=1)).isoformat()
    with pytest.raises(ValueError,match='differs from raw'):runner.price_packet(q,tmp_path,x['event'])
    q=quote(x,tmp_path,now);q['source_product']['betNm']='wrong product label'
    with pytest.raises(ValueError,match='differs from raw'):runner.price_packet(q,tmp_path,x['event'])


def test_schedule_outage_still_closes_missed_cutoff(tmp_path):
    x=payload();p=tmp_path/'paired-ledger.jsonl'
    ledger.observe_opportunity(x['event'],p,{'test_only':True},clock=lambda:old.NOW)
    class Broken:
        count=1
        def schedule(self,dates):raise TimeoutError('synthetic source outage')
    late=ledger.moment(x['event']['kickoff_at'])-timedelta(minutes=29)
    d=runner.run_once(tmp_path,Broken(),lambda *a:None,clock=lambda:late,infer=old.fake_infer)
    assert d['status']=='failed' and d['comparison']['closed_without_any_prediction']==1
    missed=[r for r in ledger.read(p) if r['type']=='missed_cutoff'][0]
    assert missed['last_exclusion']['reason']=='source_collection_failed'
