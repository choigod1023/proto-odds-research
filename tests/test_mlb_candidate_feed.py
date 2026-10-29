from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mlb_candidate_feed import collect, extract, MAX_BODY

RECEIVED='2026-10-29T13:00:00Z'
START='2026-10-29T15:00:00Z'


def feed():
    teams={}
    for side,base in [('home',10),('away',30)]:
        teams[side]=dict(battingOrder=list(range(base,base+9)),players={
            'ID'+str(i):dict(person=dict(id=i,fullName=str(i)),
                            seasonStats=dict(batting=dict(plateAppearances=100,hits=20),
                                             pitching=dict(outs=30,earnedRuns=4)),
                            stats={'batting':{'homeRuns':99}}, irrelevant='discard')
            for i in range(base,base+10)})
    return dict(gamePk=1,metaData=dict(timeStamp='20261029_125959'),
                gameData=dict(datetime=dict(dateTime=START),status=dict(codedGameState='S',detailedState='Scheduled'),
                              probablePitchers=dict(home={'id':19},away={'id':39})),
                liveData=dict(plays=dict(allPlays=[]),boxscore=dict(teams=teams)))


def extract_one(raw, **kw):
    return extract(raw,game_pk=1,scheduled_at=START,received_at=RECEIVED,
                   source_url='https://statsapi.mlb.com/api/v1.1/game/1/feed/live',
                   response_sha256='hash',**kw)


def test_extract_exact_order_prior_stats_only_no_current_event_results():
    raw=feed(); original=deepcopy(raw)
    result=extract_one(raw)
    assert result['status']=='captured'
    assert result['cutoff_at']==RECEIVED and result['source_kind']=='official_live_completed_http'
    assert [p['id'] for p in result['sides']['home']['lineup']]==list(range(10,19))
    assert result['sides']['home']['lineup'][0]['batting']=={'plateAppearances':100,'hits':20}
    assert 'stats' not in result['sides']['home']['lineup'][0]
    assert not result['starter_final_confirmed']
    assert raw==original


@pytest.mark.parametrize('defect', ['late','future_source','start_mismatch','different_fixture','live','pitch','no_lineup','duplicate','missing_player'])
def test_fail_closed_on_unannounced_or_post_event_feed(defect):
    raw=feed()
    if defect=='late':raw['gameData']['datetime']['dateTime']=RECEIVED
    if defect=='future_source':raw['metaData']['timeStamp']='20261029_130001'
    if defect=='start_mismatch':raw['gameData']['datetime']['dateTime']='2026-10-29T16:00:00Z'
    if defect=='different_fixture':raw['gamePk']=2
    if defect=='live':raw['gameData']['status']['codedGameState']='I'
    if defect=='pitch':raw['liveData']['plays']['allPlays']=[{'playEvents':[{'isPitch':True}]}]
    if defect=='no_lineup':raw['liveData']['boxscore']['teams']['home']['battingOrder']=[]
    if defect=='duplicate':raw['liveData']['boxscore']['teams']['home']['battingOrder']=[10]*9
    if defect=='missing_player':raw['liveData']['boxscore']['teams']['home']['players'].pop('ID10')
    with pytest.raises(ValueError):extract_one(raw)


class Response:
    def __init__(self,body):self.body=body;self.closed=False
    def raise_for_status(self):pass
    def iter_content(self,**kwargs):yield self.body
    def close(self):self.closed=True


class Session:
    def __init__(self,body):self.calls=[];self.responses=[];self.body=body
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        r=Response(self.body);self.responses.append(r);return r


def schedule(pk=1,date=START,state='S'):
    return dict(gamePk=pk,gameDate=date,status={'codedGameState':state})


def test_network_is_bounded_and_received_time_is_after_body_not_request_start():
    s=Session(json.dumps(feed()).encode())
    now=datetime(2026,10,29,12,59,0,tzinfo=timezone.utc)
    received=datetime(2026,10,29,13,0,0,tzinfo=timezone.utc)
    results=collect([schedule()],s,now=now,clock=lambda:received)
    assert results['1']['observed_at']==received.isoformat()
    assert s.calls[0][1]=={'timeout':(5,10),'stream':True}
    assert all(r.closed for r in s.responses)
    s=Session(b'bad')
    collect([schedule(i) for i in range(1,8)],s,now=now,clock=lambda:received)
    assert len(s.calls)==3


def test_size_failure_and_missing_lineups_do_not_discard_other_player_collection():
    now=datetime(2026,10,29,13,0,tzinfo=timezone.utc)
    s=Session(b'x'*(MAX_BODY+1))
    r=collect([schedule()],s,now=now,clock=lambda:now)
    assert r['1']['status']=='unavailable' and r['1']['reason']=='response_size_limit'
    assert s.responses[0].closed
    raw=feed();raw['liveData']['boxscore']['teams']['home']['battingOrder']=[]
    r=collect([schedule()],Session(json.dumps(raw).encode()),now=now,clock=lambda:now)
    assert r['1']['reason']=='starter_or_complete_lineup_missing'


def test_no_calls_for_live_started_distant_or_t30_fixtures():
    now=datetime(2026,10,29,13,0,tzinfo=timezone.utc)
    s=Session(b'')
    assert collect([schedule(1,state='F'),schedule(2,date='2026-10-30T15:00:00Z'),
                    schedule(3,date='2026-10-29T13:30:00Z')],s,now=now,clock=lambda:now)=={}
    assert not s.calls


def test_bounded_rotation_does_not_always_fetch_the_same_first_three_games():
    now=datetime(2026,10,29,13,0,tzinfo=timezone.utc)
    games=[schedule(i,date='2026-10-29T16:00:00Z') for i in range(1,7)]
    first,second=Session(b'bad'),Session(b'bad')
    collect(games,first,now=now,clock=lambda:now)
    later=datetime(2026,10,29,13,30,tzinfo=timezone.utc)
    collect(games,second,now=later,clock=lambda:later)
    assert len(first.calls)==len(second.calls)==3
    assert {c[0] for c in first.calls}.isdisjoint({c[0] for c in second.calls})
