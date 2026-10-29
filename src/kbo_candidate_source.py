"""Official calendar coverage and a small daily cache for the frozen KBO arm.

Run only in the existing background player collector. No history or network is
read when an individual candidate is inferred. The seed is first observed now,
never given a fabricated historical availability time.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
import requests

KST=timezone(timedelta(hours=9))
URL='https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList'
VERSION='kbo-candidate-calendar-v1'


def day_manifest(body, *, day, observed_at, source_sha256):
    from kbo_frozen_candidate import TEAMS
    if (not isinstance(body,dict) or str(body.get('code'))!='100'
            or not isinstance(body.get('game'),list) or len(body['game'])>20):
        raise ValueError('invalid_official_calendar')
    games,results=[],[]
    received=datetime.fromisoformat(observed_at)
    if received.utcoffset() is None:
        raise ValueError('timezone_required')
    for r in body['game']:
        if r.get('G_DT')!=day.replace('-','') or str(r.get('LE_ID'))!='1':
            raise ValueError('calendar_date_or_league_mismatch')
        home,away=str(r.get('HOME_NM','')).strip(),str(r.get('AWAY_NM','')).strip()
        if home not in TEAMS or away not in TEAMS or home==away or not r.get('G_ID'):
            raise ValueError('calendar_team_or_game_identity_invalid')
        gid='kbo:'+str(r['G_ID'])
        if str(r.get('CANCEL_SC_ID'))!='0':
            status='cancelled'
        elif str(r.get('GAME_STATE_SC'))=='3' and str(r.get('GAME_RESULT_CK'))=='1':
            status='final'
        else:
            status='pending'
        game=dict(source_fixture_id=gid,home=home,away=away,status=status)
        if status=='final':
            start=datetime.strptime(r['G_DT']+' '+r['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=KST)
            if received<=start:
                raise ValueError('official_final_observed_before_start')
            hs,aws=int(r['B_SCORE_CN']),int(r['T_SCORE_CN'])
            if hs<0 or aws<0:
                raise ValueError('negative_score')
            game.update(hg=hs,ag=aws)
            results.append(dict(source_fixture_id=gid,league='KBO',date=day,home=home,away=away,
                                hg=hs,ag=aws,available_at=observed_at,
                                availability_basis='official_final_observed_at'))
        games.append(game)
    if len({g['source_fixture_id'] for g in games})!=len(games):
        raise ValueError('duplicate_official_game')
    manifest=dict(date=day,league='KBO',source='kbo_official_calendar',
                  complete=all(g['status']!='pending' for g in games),observed_at=observed_at,
                  source_sha256=source_sha256,games=games)
    return dict(manifest=manifest,results=results,source_url=URL)


def collect(existing=None, *, session=None, clock=None):
    from kbo_frozen_candidate import (historical_seed,SEED_HASH,signature,
                                      verify_increment_coverage,build_daily_state)
    clock=clock or (lambda:datetime.now(timezone.utc))
    now=clock()
    if now.utcoffset() is None:
        raise ValueError('timezone_required')
    if session is None:
        with requests.Session() as owned:
            owned.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx'})
            return collect(existing,session=owned,clock=clock)
    old=existing if isinstance(existing,dict) and existing.get('seed_hash')==SEED_HASH else {}
    seed=historical_seed()
    seed_seen=old.get('seed_observed_at') or clock().astimezone(timezone.utc).isoformat()
    first=datetime.fromisoformat(seed['through_date']).date()+timedelta(days=1)
    today=now.astimezone(KST).date()
    if not 0 <= (today-first).days < 400:
        return dict(version=VERSION,status='unavailable',reason='seed_history_window_requires_review',daily_states={})
    days=[(first+timedelta(days=i)).isoformat() for i in range((today-first).days+1)]
    cache={d:deepcopy(v) for d,v in (old.get('calendar_cache') or {}).items() if d in days}
    wanted=list(dict.fromkeys([d for d in days if d not in cache]+days[-2:]))[:4]
    errors={}
    deadline=time.monotonic()+30
    for day in wanted:
        if time.monotonic()>=deadline:
            errors[day]='collection_time_limit'
            cache.pop(day,None)
            continue
        response=None
        try:
            response=session.post(URL,data={'leId':'1','srId':'0,1,3,4,5,6,7,8,9','date':day.replace('-','')},
                                  timeout=(3,10),stream=True)
            response.raise_for_status()
            parts=[];size=0
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic()>deadline:raise ValueError('collection_time_limit')
                size+=len(chunk)
                if size>512*1024:raise ValueError('calendar_size_limit')
                parts.append(chunk)
            raw=b''.join(parts)
            received=clock().astimezone(timezone.utc).isoformat()
            item=day_manifest(json.loads(raw),day=day,observed_at=received,
                              source_sha256=hashlib.sha256(raw).hexdigest())
            # An empty response cannot silently erase known fixtures.
            if cache.get(day,{}).get('manifest',{}).get('games') and not item['manifest']['games']:
                raise ValueError('empty_calendar_contradicts_cached_games')
            cache[day]=item
        except Exception as exc:
            errors[day]=str(exc) if isinstance(exc,ValueError) and str(exc).isidentifier() else type(exc).__name__
            cache.pop(day,None)  # no quiet fallback to a now-unverified day
        finally:
            if response is not None:response.close()
    observed=clock().astimezone(timezone.utc).isoformat()
    states={}
    for target in (today,today+timedelta(days=1)):
        target_day=target.isoformat()
        needed=[d for d in days if d<target_day]
        missing=[d for d in needed if d not in cache]
        if missing:
            states[target_day]=dict(status='unavailable',reason='missing_calendar_days',missing_days=missing[:8])
            continue
        manifests=[cache[d]['manifest'] for d in needed]
        results=[r for d in needed for r in cache[d]['results']]
        coverage=verify_increment_coverage(schedule_days=manifests,results=results,
            coverage_from=first.isoformat(),complete_through=(target-timedelta(days=1)).isoformat(),
            as_of=observed,source_id=URL)
        if coverage['status']!='available':
            states[target_day]=coverage
            continue
        source=coverage['source_signature']
        source.update(seed_hash=SEED_HASH,seed_observed_at=seed_seen,
                      seed_history_unchanged=signature(seed)==SEED_HASH)
        states[target_day]=build_daily_state(state_date=target_day,as_of=observed,results=results,
                                             source_signature=source,seed=seed)
    return dict(version=VERSION,status='collected',observed_at=observed,seed_hash=SEED_HASH,
                seed_observed_at=seed_seen,calendar_cache=cache,daily_states=states,errors=errors,
                limitations=['Seed history is a fixed research archive; historical availability and later corrections are unproven.',
                             'Calendar coverage authenticates only the increment after the seed, not the entire historical archive.'])
