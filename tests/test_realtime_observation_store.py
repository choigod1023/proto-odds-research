import copy
import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

PATH=Path(__file__).resolve().parents[1]/'src'/'realtime_observation_store.py'
spec=importlib.util.spec_from_file_location('realtime_observation_store', PATH)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
RAW=b'{"source":"provider"}'
T0='2026-10-31T10:00:00+00:00'
KICK='2026-10-31T11:00:00+00:00'
AGES={'odds':300,'lineup':3600,'injury':3600,'substitution':300}


def event(seq=1, received=T0, *, kind='odds', phase='pregame', payload=None, raw=RAW):
    if payload is None:
        payload={'provider_product_id':'product-1','market':'moneyline','period':'full','line':None,
                 'settlement_scope':'full_including_ot','status':'open','outcome_ids':['home','away'],
                 'outcomes':{'home':1.8,'away':2.2},'quote_at_claim':T0,'valid_until':'2026-10-31T10:30:00+00:00'}
    return {'schema_version':m.SCHEMA,'provider':'fixture','provider_event_id':'feed-event',
            'provider_revision_id':f'rev-{seq}','provider_game_id':'official-game-1',
            'canonical_game_id':'canonical-1','stream_id':f'{kind}-stream','sequence':seq,'kind':kind,
            'phase':phase,'kickoff_at':KICK,'source_url':'https://example.org/feed',
            'source_published_at_claim':'2026-10-31T09:59:00+00:00','received_at':received,
            'source_sha256':hashlib.sha256(raw).hexdigest(),'external_receipt_verified':False,'payload':payload}


@pytest.fixture
def store():
    c=sqlite3.connect(':memory:'); yield m.ObservationStore(c); c.close()


def test_raw_roundtrip_duplicate_conflict_and_first_receipt(store):
    e=event(); a=store.append(e,RAW)
    assert a['action']=='recompute_prediction'
    assert store.raw_revision('fixture','official-game-1','odds','odds-stream','rev-1')==RAW
    retry=copy.deepcopy(e); retry['received_at']='2026-10-31T10:01:00+00:00'
    assert store.append(retry,RAW)=={'status':'duplicate','action':'none','first_received_at':'2026-10-31T10:00:00.000000+00:00'}
    retry['payload']['outcomes']['home']=1.9
    with pytest.raises(m.ObservationError,match='revision content conflict'): store.append(retry,RAW)
    with pytest.raises(m.ObservationError,match='hash mismatch'): store.append(event(),b'wrong')


def test_out_of_order_does_not_override_newer_or_backdate(store):
    e=event(3); store.append(e,RAW)
    older=event(1,'2026-10-31T10:01:00+00:00'); older['payload']['outcomes']['home']=1.5
    assert store.append(older,RAW)['status']=='out_of_order'
    snap=store.snapshot('canonical-1','2026-10-31T10:02:00+00:00','pregame',KICK,AGES)
    assert snap['entries'][0]['observation']['sequence']==3
    with pytest.raises(m.ObservationError,match='receipt order reversal'): store.append(event(4),RAW)
    assert store.db.execute('SELECT count(*) FROM observation_event_v1').fetchone()[0]==2


def test_changed_closed_market_blocks_stale_fallback_and_correction(store):
    store.append(event(),RAW)
    closed=event(2,'2026-10-31T10:01:00+00:00');closed['payload']['status']='suspended';closed['payload']['outcomes']={};closed['payload']['valid_until']=None;closed['payload']['quote_at_claim']=None
    assert store.append(closed,RAW)['action']=='invalidate'
    snap=store.snapshot('canonical-1','2026-10-31T10:02:00+00:00','pregame',KICK,AGES)
    assert len(snap['entries'])==1 and snap['entries'][0]['reason']=='suspended'
    fixed=event(3,'2026-10-31T10:03:00+00:00'); fixed['supersedes_revision_id']='rev-2';fixed['payload']['outcomes']['away']=2.4
    assert store.append(fixed,RAW)['action']=='recompute_prediction'
    assert store.snapshot('canonical-1','2026-10-31T10:04:00+00:00','pregame',KICK,AGES)['entries'][0]['usable']
    assert store.snapshot('canonical-1','2026-10-31T10:10:00+00:00','pregame',KICK,AGES)['entries'][0]['reason']=='stale'
    with pytest.raises(m.ObservationError,match='cutoff'):store.snapshot('canonical-1','2026-10-31T10:31:00+00:00','pregame',KICK,AGES)


def test_freshness_revisions_and_market_identity_are_separate(store):
    store.append(event(),RAW)
    same=event(2,'2026-10-31T10:01:00+00:00')
    assert store.append(same,RAW)['action']=='refresh_freshness'
    bad=event(3,'2026-10-31T10:02:00+00:00');bad['payload']['period']='first_half'
    with pytest.raises(m.ObservationError,match='payload identity'): store.append(bad,RAW)
    bad=event(3,'2026-10-31T10:02:00+00:00');bad['payload']['outcomes'].pop('away')
    with pytest.raises(m.ObservationError,match='incomplete'): store.append(bad,RAW)
    bad=event(3,'2026-10-31T10:02:00+00:00');bad['supersedes_revision_id']='missing'
    with pytest.raises(m.ObservationError,match='correction'): store.append(bad,RAW)


def test_asof_no_future_or_result_leak_and_live_phase(store):
    store.append(event(),RAW)
    player={'provider_team_id':'home-team','provider_player_id':'player-1','status':'out'}
    injury=event(1,'2026-10-31T10:01:00+00:00',kind='injury',payload=player); store.append(injury,RAW)
    sub=event(1,'2026-10-31T11:01:00+00:00',kind='substitution',phase='inplay',payload={'provider_team_id':'home-team','player_in_id':'p2','player_out_id':'player-1'});store.append(sub,RAW)
    result=event(1,'2026-10-31T11:05:00+00:00',kind='settlement',phase='inplay',payload={'provider_product_id':'product-1','settlement_scope':'full_including_ot','status':'final','result':{'home':1,'away':0},'final_at':'2026-10-31T11:04:00+00:00'})
    assert store.append(result,RAW)['action']=='settlement_only'
    old=store.snapshot('canonical-1','2026-10-31T10:00:00+00:00','pregame',KICK,AGES)
    assert [x['observation']['kind'] for x in old['entries']]==['odds']
    live=store.snapshot('canonical-1','2026-10-31T11:06:00+00:00','inplay',KICK,AGES)
    assert {x['observation']['kind'] for x in live['entries']}=={'odds','injury','substitution'}
    assert live['purpose']=='prediction_recalculation_only' and len(live['provenance_sha256'])==64
    assert store.snapshot('canonical-1','2026-10-31T10:00:00+00:00','pregame',KICK,AGES)==old
    with pytest.raises(m.ObservationError,match='inplay before kickoff'): store.snapshot('canonical-1',T0,'inplay',KICK,AGES)


def test_late_pregame_record_never_leaks_into_inplay_snapshot(store):
    late=event(1,'2026-10-31T11:01:00+00:00',kind='injury',payload={'provider_team_id':'h','provider_player_id':'x','status':'out'})
    assert store.append(late,RAW)['status']=='appended'
    entries=store.snapshot('canonical-1','2026-10-31T11:02:00+00:00','inplay',KICK,AGES)['entries']
    assert len(entries)==1 and entries[0]['reason']=='late_pregame' and not entries[0]['usable']


def test_input_validation_fails_closed(store):
    for field,value in [('external_receipt_verified',True),('received_at','2026-10-31T10:00:00'),('sequence',True),('source_published_at_claim','2026-10-31T10:01:00+00:00')]:
        e=event();e[field]=value
        with pytest.raises(m.ObservationError):store.append(e,RAW)
    e=event(); e['payload']['outcomes']['home']=float('nan')
    with pytest.raises(m.ObservationError):store.append(e,RAW)
    with pytest.raises(m.ObservationError):store.snapshot('canonical-1',T0,'pregame',KICK,{'odds':300})


def test_lineup_revisions_bind_player_ids_and_raw_corruption(store):
    lineup={'provider_team_id':'h','announcement_status':'probable','players':[{'provider_player_id':'p1','role':'starter','order':1}]}
    e=event(kind='lineup',payload=lineup);store.append(e,RAW)
    updated=event(2,'2026-10-31T10:01:00+00:00',kind='lineup',payload=copy.deepcopy(lineup));updated['payload']['players'][0]['provider_player_id']='p2'
    assert store.append(updated,RAW)['action']=='recompute_prediction'
    store.db.execute('UPDATE observation_event_v1 SET raw=? WHERE revision_id=?',(b'corrupted','rev-1')); store.db.commit()
    with pytest.raises(m.ObservationError,match='stored raw hash'):store.raw_revision('fixture','official-game-1','lineup','lineup-stream','rev-1')

def test_late_pregame_change_does_not_authorize_frozen_pick_change(store):
    store.append(event(), RAW)
    changed=event(2,'2026-10-31T10:31:00+00:00')
    changed['payload']['valid_until']='2026-10-31T10:59:00+00:00'
    changed['payload']['outcomes']['home']=1.9
    assert store.append(changed,RAW)['action']=='pregame_frozen'
    frozen=store.snapshot('canonical-1','2026-10-31T10:30:00+00:00','pregame',KICK,AGES)
    assert frozen['entries'][0]['observation']['provider_revision_id']=='rev-1'

def test_persistent_reopen_and_idempotent_concurrent_ingestion(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    db=tmp_path/'observations.sqlite'
    sqlite3.connect(db).close()
    m.ObservationStore(sqlite3.connect(db)).db.close()
    def ingest(_):
        c=sqlite3.connect(db, timeout=5)
        try:return m.ObservationStore(c).append(event(),RAW)
        finally:c.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(ingest,range(2)))
    assert sorted(x['status'] for x in results)==['appended','duplicate']
    c=sqlite3.connect(db); s=m.ObservationStore(c)
    assert len(s.snapshot('canonical-1',T0,'pregame',KICK,AGES)['entries'])==1
    c.close()


def test_no_mutation_on_colliding_sequence_or_wrong_stream_identity(store):
    store.append(event(),RAW)
    bad=event(1,'2026-10-31T10:02:00+00:00');bad['provider_revision_id']='different'
    with pytest.raises(m.ObservationError,match='sequence conflict'):store.append(bad,RAW)
    bad=event(2,'2026-10-31T10:02:00+00:00');bad['provider_game_id']='other'; bad['canonical_game_id']='canonical-other'
    assert store.append(bad,RAW)['status']=='appended'
    assert len(store.snapshot('canonical-1',T0,'pregame',KICK,AGES)['entries'])==1
    bad=event(3,'2026-10-31T10:03:00+00:00');bad['canonical_game_id']='canonical-other'
    with pytest.raises(m.ObservationError,match='identity conflict'):store.append(bad,RAW)


def test_receiving_old_quote_again_does_not_make_quote_fresh(store):
    store.append(event(),RAW)
    refresh=event(2,'2026-10-31T10:10:00+00:00')
    assert store.append(refresh,RAW)['action']=='refresh_freshness'
    snap=store.snapshot('canonical-1','2026-10-31T10:10:00+00:00','pregame',KICK,AGES)
    assert snap['entries'][0]['reason']=='stale'
    bad=event(3,'2026-10-31T10:11:00+00:00');bad['payload']['quote_at_claim']='2026-10-31T10:12:00+00:00'
    with pytest.raises(m.ObservationError,match='quote claim after receipt'):store.append(bad,RAW)


def test_late_pregame_update_blocks_older_live_price_fallback(store):
    old=event();old['payload']['valid_until']='2026-10-31T11:30:00+00:00';store.append(old,RAW)
    late=event(2,'2026-10-31T11:01:00+00:00');late['payload']['status']='suspended';late['payload']['outcomes']={};late['payload']['valid_until']=None;late['payload']['quote_at_claim']=None
    assert store.append(late,RAW)['action']=='pregame_frozen'
    ages={**AGES,'odds':7200}
    entry=store.snapshot('canonical-1','2026-10-31T11:02:00+00:00','inplay',KICK,ages)['entries'][0]
    assert not entry['usable'] and entry['reason']=='late_pregame' and entry['observation']['provider_revision_id']=='rev-2'


def test_expired_on_receipt_is_preserved_and_invalidates_price(store):
    e=event(); e['payload']['valid_until']='2026-10-31T09:59:30+00:00'; e['payload']['quote_at_claim']='2026-10-31T09:59:00+00:00'
    assert store.append(e,RAW)['action']=='invalidate'
    entry=store.snapshot('canonical-1',T0,'pregame',KICK,AGES)['entries'][0]
    assert entry['reason']=='expired' and not entry['usable']
    assert store.raw_revision('fixture','official-game-1','odds','odds-stream','rev-1')==RAW


def test_settlement_final_time_cannot_be_after_receipt_or_before_kickoff(store):
    p={'provider_product_id':'p','settlement_scope':'full','status':'final','result':{'home':1,'away':0},'final_at':'2026-10-31T11:06:00+00:00'}
    e=event(1,'2026-10-31T11:05:00+00:00',kind='settlement',phase='inplay',payload=p)
    with pytest.raises(m.ObservationError,match='final after receipt'):store.append(e,RAW)
    e['payload']['final_at']='2026-10-31T10:50:00+00:00'
    with pytest.raises(m.ObservationError,match='final before kickoff'):store.append(e,RAW)
