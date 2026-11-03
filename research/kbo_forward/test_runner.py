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
        x=copy.deepcopy(self.x);x.pop('market');x['evidence'].pop('odds')
        return {'source_verified':True,'payload':x}
    def result(self,game):return {'verified':False}

def quote(x):
    return {**{k:x['event'][k] for k in ('official_game_id','season','home_id','away_id','kickoff_at')},'market':copy.deepcopy(x['market']),'evidence':copy.deepcopy(x['evidence']['odds'])}

def test_actual_runner_persists_paired_capture_and_restart_is_idempotent(tmp_path):
    x=payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(hours=2)
    x["evidence"]["odds"]["received_at"]=now.isoformat();official=Official(x);prices=lambda *a:{'quotes':[quote(x)],'excluded':[],'requests':[]}
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

def test_postdeadline_run_does_not_backfill_or_fetch_prices(tmp_path):
    x=payload();now=ledger.moment(x['event']['kickoff_at'])-timedelta(minutes=29);o=Official(x)
    def forbidden(*a):raise AssertionError('unnecessary price request')
    d=runner.run_once(tmp_path,o,forbidden,clock=lambda:now,infer=old.fake_infer)
    assert d['status']=='ok' and o.snapshot_calls==0 and not (tmp_path/'paired-ledger.jsonl').exists()

def test_process_scoped_lock_blocks_another_writer_and_releases(tmp_path):
    from locking import single_writer
    p=tmp_path/'x.lock'
    with single_writer(p) as first:
        assert first
        with single_writer(p) as second:assert not second
    with single_writer(p) as third:assert third
