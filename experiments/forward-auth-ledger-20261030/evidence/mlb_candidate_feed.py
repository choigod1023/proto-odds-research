"""Bounded pregame feed acquisition inside the existing player collector.

Only explicit scheduled/warmup feeds with both announced lineups qualify. Raw
HTTP bodies are hashed; only the exact pregame player fields used by the frozen
candidate are retained. No predictions or calls occur on the HTTP-serving path.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
import requests

VERSION = 'mlb-candidate-feed-v1'
MAX_BODY = 2 * 1024 * 1024
MAX_GAMES = 3
BAT_FIELDS = ('plateAppearances','strikeOuts','baseOnBalls','atBats','hitByPitch',
              'sacFlies','hits','totalBases')
PIT_FIELDS = ('battersFaced','strikeOuts','baseOnBalls','outs','earnedRuns')


def stamp(value):
    if not isinstance(value,str):
        raise ValueError('missing_timestamp')
    if len(value)==15 and value[8]=='_':
        return datetime.strptime(value,'%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
    at=datetime.fromisoformat(value.replace('Z','+00:00'))
    if at.utcoffset() is None:
        raise ValueError('timezone_required')
    return at.astimezone(timezone.utc)


def extract(feed, *, game_pk, scheduled_at, received_at, source_url, response_sha256):
    """Pure extraction. completed HTTP time is the true forward availability."""
    received, start=stamp(received_at),stamp(scheduled_at)
    if not received < start-timedelta(minutes=30):
        raise ValueError('not_before_t30')
    if feed.get('gamePk') != game_pk:
        raise ValueError('different_fixture_returned')
    source_at=stamp((feed.get('metaData') or {}).get('timeStamp'))
    data=feed.get('gameData') or {}
    feed_start=stamp((data.get('datetime') or {}).get('dateTime'))
    if feed_start != start or not source_at <= received < feed_start-timedelta(minutes=30):
        raise ValueError('source_clock_or_fixture_start_mismatch')
    if (data.get('status') or {}).get('codedGameState') not in {'S','P'}:
        raise ValueError('not_scheduled_or_warmup')
    plays=(feed.get('liveData') or {}).get('plays') or {}
    all_plays=list(plays.get('allPlays') or [])
    if plays.get('currentPlay'):
        all_plays.append(plays['currentPlay'])
    if any(event.get('isPitch') for play in all_plays for event in play.get('playEvents',[])):
        raise ValueError('pitch_already_thrown')
    boxes=((feed.get('liveData') or {}).get('boxscore') or {}).get('teams') or {}
    sides={}
    for side in ('home','away'):
        starter=((data.get('probablePitchers') or {}).get(side) or {}).get('id')
        box=boxes.get(side) or {}
        order=box.get('battingOrder') or []
        if type(starter) is not int or len(order)!=9 or len(set(order))!=9 or any(type(p) is not int for p in order):
            raise ValueError('starter_or_complete_lineup_missing')
        def player(pid):
            row=(box.get('players') or {}).get('ID'+str(pid))
            if not isinstance(row,dict) or (row.get('person') or {}).get('id',pid)!=pid:
                raise ValueError('missing_or_mismatched_player')
            stats=row.get('seasonStats') or {}
            return dict(id=pid,name=(row.get('person') or {}).get('fullName',''),
                        batting={k:deepcopy(stats.get('batting',{}).get(k)) for k in BAT_FIELDS if k in stats.get('batting',{})},
                        pitching={k:deepcopy(stats.get('pitching',{}).get(k)) for k in PIT_FIELDS if k in stats.get('pitching',{})})
        sides[side]=dict(starter=player(starter),lineup=[player(pid) for pid in order])
    if ({p['id'] for p in sides['home']['lineup']} & {p['id'] for p in sides['away']['lineup']}
            or sides['home']['starter']['id']==sides['away']['starter']['id']):
        raise ValueError('opponents_share_player_identity')
    return dict(version=VERSION,status='captured',gamePk=game_pk,
                source_kind='official_live_completed_http',source_url=source_url,
                response_sha256=response_sha256,observed_at=received_at,
                snapshot_at=source_at.isoformat(),cutoff_at=received_at,
                start_at=start.isoformat(),feed_start_at=feed_start.isoformat(),
                source_status=(data.get('status') or {}).get('detailedState'),sides=sides,
                starter_identity_kind='official_published_probable_pitcher',
                starter_final_confirmed=False,lineup_identity_kind='official_boxscore_batting_order',
                affects_selection=False,affects_probability=False)


def collect(games, session=None, *, now=None, clock=None, request_limit=MAX_GAMES,
            slot_seconds=1800, request_timeout=(5,10), budget_seconds=35):
    """At most three feeds, streamed <=2MiB each, one attempt, bounded timeout.

    Returns a detached fixture-keyed capture map; failures never discard normal
    player information or turn a missing lineup into an announced one.
    """
    clock=clock or (lambda:datetime.now(timezone.utc))
    now=now or clock()
    if now.utcoffset() is None:
        raise ValueError('timezone_required')
    upcoming=[]
    for game in games:
        try:
            start=stamp(game['gameDate'])
            if (now+timedelta(minutes=30)<start<=now+timedelta(hours=6)
                    and ((game.get('status') or {}).get('codedGameState') in {'S','P'}
                         or game.get('request_eligibility')=='cached_future_fixture')
                    and type(game.get('gamePk')) is int):
                upcoming.append((start,game['gamePk'],game))
        except (KeyError,TypeError,ValueError):
            continue
    if session is None:
        # The normal player collector retries requests. The optional research
        # source has a separate zero-retry budget and must not inherit that.
        with requests.Session() as bounded:
            bounded.headers.update({'User-Agent':'proto-odds-research/1.0','Accept':'application/json'})
            return collect(games,bounded,now=now,clock=clock,request_limit=request_limit,
                           slot_seconds=slot_seconds,request_timeout=request_timeout,budget_seconds=budget_seconds)
    captures={}
    deadline=time.monotonic()+budget_seconds
    ordered=sorted(upcoming,key=lambda r:(r[0],r[1]))
    if ordered:
        cursor=(int(now.timestamp())//slot_seconds*request_limit)%len(ordered)
        ordered=ordered[cursor:]+ordered[:cursor]
    for start,pk,game in ordered[:request_limit]:
        if time.monotonic()>=deadline:
            break
        url=f'https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live'
        record=dict(version=VERSION,status='unavailable',reason='request_failed',
                    source_url=url,gamePk=pk,affects_selection=False,affects_probability=False)
        response=None
        try:
            response=session.get(url,timeout=request_timeout,stream=True)
            response.raise_for_status()
            chunks=[]
            size=0
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic()>deadline:
                    raise ValueError('collection_time_limit')
                if not chunk:
                    continue
                size+=len(chunk)
                if size>MAX_BODY:
                    raise ValueError('response_size_limit')
                chunks.append(chunk)
            raw=b''.join(chunks)
            received=clock().astimezone(timezone.utc).isoformat()
            record=extract(json.loads(raw),game_pk=pk,scheduled_at=game['gameDate'],
                           received_at=received,source_url=url,response_sha256=hashlib.sha256(raw).hexdigest())
        except Exception as exc:
            # Source text may include a URL or response body: never store it.
            reason=str(exc) if isinstance(exc,ValueError) and str(exc).isidentifier() else type(exc).__name__
            record.update(reason=reason,observed_at=clock().astimezone(timezone.utc).isoformat())
        finally:
            if response is not None:
                response.close()
        captures[str(pk)]=record
    return captures
