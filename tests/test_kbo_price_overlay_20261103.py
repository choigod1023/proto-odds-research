import copy,importlib.util,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments'/'kbo-player-model-20261103'/'phase3_overlay.py'
spec=importlib.util.spec_from_file_location('kbo_price_overlay',P);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)

def setup_case(kickoff=True):
    start=datetime.fromisoformat('2026-03-31T18:30:00+09:00');ms=int(start.timestamp()*1000)
    row={'fixture_id':'fixture','season':2026,'event_date':'2026-03-31','home_id':'삼성','away_id':'두산','kickoff_at':start.isoformat() if kickoff else None,'market':None}
    label={'status':'final','home_score':5,'away_score':5}
    candidate={'leagueCode':'BS001','betId':'2','betTypId':'2','handi':21,'homeId':'SS','awayId':'OB','drawAllot':0,'gameSubject':'','gameReject':'0','buyReject':'0','mchScore':'5:5','gameDate':ms,'winAllot':1.61,'loseAllot':1.89,'matchSeq':192}
    attempt={'body_sha256':'a'*64,'received_at':'2026-11-03T01:00:00+00:00'}
    detail={'body':'evidence/a.body.gz','attempt':attempt,'rows':[candidate]}
    schedule={'gmOsidTsYear':2026,'saleStartDate':ms-86400000,'saleEndDate':ms+86400000,'gmTs':'260040'}
    rec={'fixture_id':'fixture','market_id':'proto:2026:40:192:full_game_2way','official_round_id':260040,'match_seq':192,'official_home_id':'SS','official_away_id':'OB','game_at':start.isoformat(),'score':'5:5','prices':{'home':1.61,'away':1.89},'source_observed_at':None,'received_at':attempt['received_at'],'response_sha256':attempt['body_sha256'],'response_file':detail['body'],'execution_price_verified':False,'source_kickoff_matched':kickoff}
    return row,label,rec,[schedule],{'260040':detail}


def test_official_match_and_date_only_are_separate():
    for known in (True,False):
        row,label,rec,listing,official=setup_case(known)
        info,reason=m.classify(row,label,rec,listing,official,{('2026-03-31','삼성','두산'):1})
        assert reason is None and info['kickoff_matched']==known
        assert info['official_evidence']['source_observed_at'] is None and not info['official_evidence']['execution_price_verified']

def test_reject_mismatch_ambiguous_and_bad_market():
    row,label,rec,listing,official=setup_case(False);counts={('2026-03-31','삼성','두산'):1}
    for change in ({'winAllot':1},{'mchScore':'4:5'},{'homeId':'OB'},{'buyReject':'1'},{'betId':'9'},{'gameDate':0}):
        altered=copy.deepcopy(official);altered['260040']['rows'][0].update(change)
        assert m.classify(row,label,rec,listing,altered,counts)[0] is None
    duplicate=copy.deepcopy(official);duplicate['260040']['rows']*=2
    assert m.classify(row,label,rec,listing,duplicate,counts)[1]=='official_none_or_ambiguous'
    assert m.classify(row,label,rec,listing,official,{('2026-03-31','삼성','두산'):2})[1]=='date_only_doubleheader_ambiguous'
    wrong=copy.deepcopy(rec);wrong['source_observed_at']='2026-03-30T00:00:00+09:00'
    assert m.classify(row,label,wrong,listing,official,counts)[1]=='availability_claim_mismatch'

def test_refund_is_in_roi_denominator_and_probabilities_do_not_change():
    row,label,rec,listing,official=setup_case(False);fixtures={};labels={};forecast={k:[] for k in m.MODES}
    for i in range(726):
        fid='fixture' if i==0 else f'id{i}';x=copy.deepcopy(row);x['fixture_id']=fid
        if i:
            x['home_id']='other'+str(i)
            x['kickoff_at']='2026-03-31T18:30:00+09:00';x['market']={'market_id':f'market-{i}','kind':'full_game_2way','settlement_rule':'draw_void_refund','selections':{'home':2.,'away':2.}}
        fixtures[fid]=x;labels[fid]=copy.deepcopy(label)
        for name in m.MODES:forecast[name].append({'fixture_id':fid,'probabilities_2way':{'home':.6,'away':.4}})
    before=copy.deepcopy(forecast)
    output,ledgers=m.evaluate(fixtures,labels,forecast,[rec],listing,official,{})
    assert forecast==before
    assert output['cohorts']['strict_original_time_match']['n']==725
    ext=output['cohorts']['extended_day_team_source_confirmed'];assert ext['n']==726 and ext['draw_refunds']==726
    assert ext['models']['player']['roi_including_refunds']==0 and ext['models']['player']['accuracy_decisive'] is None
    entry=next(x for x in ledgers['extended_day_team_source_confirmed'] if x['fixture_id']=='fixture')
    assert entry['forecasts']['player']['net']=='0' and entry['price_source']['market_id']==rec['market_id']

def test_original_price_must_have_valid_product_and_clock():
    row,label,rec,listing,official=setup_case();row['market']={'market_id':'archive','kind':'full_game_2way','settlement_rule':'draw_void_refund','selections':{'home':1,'away':2}}
    assert m.classify(row,label,None,listing,official,{})[1]=='original_price_invalid'
    row['market']['selections']['home']=1.7;row['kickoff_at']=None
    assert m.classify(row,label,None,listing,official,{})[1]=='original_kickoff_missing'
    row['market']['market_id']=''
    assert m.classify(row,label,None,listing,official,{})[1]=='original_market_id_missing'

def test_closed_listing_and_worker_manifest_tampering_rejected(tmp_path,monkeypatch):
    import gzip,hashlib,json
    evidence=tmp_path/'evidence';evidence.mkdir();listing=b'{"schedules":{"data":[]}}'
    (evidence/'closed-list.body.gz').write_bytes(gzip.compress(listing))
    attempts=[{'http_status':200,'body_file':'evidence/closed-list.body.gz','body_sha256':hashlib.sha256(listing).hexdigest()}]
    raw=json.dumps(attempts).encode();(evidence/'attempts.json').write_bytes(raw)
    monkeypatch.setattr(m,'S',tmp_path);monkeypatch.setattr(m.subprocess,'check_output',lambda *args,**kw:b'[]')
    with pytest.raises(ValueError,match='manifest differs'):m.load_evidence()
    monkeypatch.setattr(m.subprocess,'check_output',lambda *args,**kw:raw)
    (evidence/'closed-list.body.gz').write_bytes(gzip.compress(b'{}'))
    with pytest.raises(ValueError,match='closed-list SHA'):m.load_evidence()


def test_corrected_source_is_exact_bytes_not_newline_normalized():
    assert m.COMMIT=='3167b4b19c2f5877af754d3a1a41ee2a8761a3f2'
    assert m.SOURCE_HASH=='d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117'
    m.exact_committed_price(b'one\r\ntwo\r\n',b'one\r\ntwo\r\n')
    with pytest.raises(ValueError,match='bytes differ'):
        m.exact_committed_price(b'one\r\ntwo\r\n',b'one\ntwo\n')

def test_checked_hash_uses_exact_price_bytes(tmp_path):
    import hashlib
    p=tmp_path/'prices.jsonl';raw=b'{"fixture":1}\r\n';p.write_bytes(raw);expected=hashlib.sha256(raw).hexdigest()
    assert m.checked(p,expected)==raw
    p.write_bytes(raw.replace(b'\r\n',b'\n'))
    with pytest.raises(ValueError,match='SHA mismatch'):m.checked(p,expected)
