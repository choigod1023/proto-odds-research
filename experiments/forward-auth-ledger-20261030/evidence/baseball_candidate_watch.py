"""Small candidate-source refresh piggybacked on the existing 5-minute job.

No model training, large player documents, old odds, inference or new daemon.
This path exists because the all-sports player job can wait for 512MB for hours.
"""
from collections import Counter
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _stamp(value):
    at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if at.utcoffset() is None:raise ValueError('timezone_required')
    return at.astimezone(timezone.utc)


def _fixtures(db,now):
    # Only five scalar identity columns cross into Python. Do not load/deserialize
    # the multi-sport document with its past player/season/injury arrays.
    with db.connect() as connection:
        rows=connection.execute("""SELECT json_extract(j.value,'$.game_id'),
            json_extract(j.value,'$.game_datetime'),json_extract(j.value,'$.home_team'),
            json_extract(j.value,'$.away_team')
            FROM documents d,json_each(d.payload_json,'$.games') j
            WHERE d.name='player_info' AND json_extract(j.value,'$.league')='MLB'
              AND julianday(json_extract(j.value,'$.game_datetime'))>julianday(?)
              AND julianday(json_extract(j.value,'$.game_datetime'))<=julianday(?)
            ORDER BY json_extract(j.value,'$.game_datetime'),json_extract(j.value,'$.game_id') LIMIT 60""",
            ((now+timedelta(minutes=30)).isoformat(),(now+timedelta(hours=6)).isoformat())).fetchall()
    counts=Counter(str(row[0]) for row in rows)
    out=[]
    for game_id,start,home,away in rows:
        try:
            pk=int(game_id);at=_stamp(start)
            if (counts[str(game_id)]!=1 or not 0<pk<1000000000 or not home or not away
                    or home==away or not now+timedelta(minutes=30)<at<=now+timedelta(hours=6)):
                continue
            out.append(dict(gamePk=pk,gameDate=start,home_team=home,away_team=away,
                            request_eligibility='cached_future_fixture'))
        except (TypeError,ValueError):
            continue
    return out


def run_once(*,db=None,clock=None):
    from runtime_db import RuntimeDatabase,database_enabled
    if db is None:
        if not database_enabled():return {'status':'disabled_without_runtime_db'}
        db=RuntimeDatabase()
    clock=clock or (lambda:datetime.now(timezone.utc))
    now=clock();previous=db.get_document('baseball_candidate_watch') or {}
    try:
        if (timedelta(0)<=now-_stamp(previous.get('checked_at'))<timedelta(minutes=4)):
            return {'status':'cooldown'}
    except (ValueError,TypeError):pass
    from mlb_candidate_feed import collect as collect_mlb
    fixtures=_fixtures(db,now)
    sources=collect_mlb(fixtures,now=now,clock=clock,request_limit=1,slot_seconds=300,
                        request_timeout=(2,3),budget_seconds=6) if fixtures else {}
    old=db.get_document('mlb_model_candidate_inputs') or {}
    current={}
    for row in old.get('games') or []:
        try:
            source=row['research_candidate_input']
            if (now+timedelta(minutes=30)<_stamp(row['game_datetime'])<=now+timedelta(hours=6)
                    and timedelta(0)<=now-_stamp(source['observed_at'])<=timedelta(minutes=45)):
                current[row['game_id']]=row
        except (ValueError,TypeError,KeyError):pass
    for f in fixtures:
        source=sources.get(str(f['gamePk'])) or {}
        if source.get('status')!='captured':continue
        current[str(f['gamePk'])]=dict(league='MLB',sport='bs',game_id=str(f['gamePk']),
            game_datetime=f['gameDate'],home_team=f['home_team'],away_team=f['away_team'],
            source='MLB Stats API',source_url=source['source_url'],updated_at=source['observed_at'],
            starters={s:{'name':source['sides'][s]['starter']['name'],
                          'player_id':source['sides'][s]['starter']['id']} for s in ('home','away')},
            lineups={s:[{'player_id':p['id'],'name':p['name'],'batting_order':i+1}
                         for i,p in enumerate(source['sides'][s]['lineup'])] for s in ('home','away')},
            research_candidate_input=source)
    if sources or current or old:
        db.put_document('mlb_model_candidate_inputs',dict(generated_at=clock().isoformat(),games=list(current.values())[:60]))
    state=db.get_document('kbo_model_candidate_state') or {}
    try:fresh=timedelta(0)<=now-_stamp(state.get('observed_at'))<timedelta(minutes=25)
    except (ValueError,TypeError):fresh=False
    if not fresh:
        try:
            from kbo_candidate_source import collect as collect_kbo
            cache=db.get_document('kbo_candidate_calendar_cache') or {}
            result=collect_kbo(cache,clock=clock,request_limit=2,request_timeout=(2,3),budget_seconds=6)
            state={k:v for k,v in result.items() if k!='calendar_cache'}
            db.put_document('kbo_candidate_calendar_cache',{k:v for k,v in result.items() if k!='daily_states'})
            db.put_document('kbo_model_candidate_state',state)
        except Exception as exc:
            state={'status':'unavailable','reason':type(exc).__name__,'daily_states':{}}
            db.put_document('kbo_model_candidate_state',state)
    report=dict(status='checked',checked_at=clock().isoformat(),mlb_eligible_fixtures=len(fixtures),
        mlb_requested=len(sources),mlb_captured=sum(s.get('status')=='captured' for s in sources.values()),
        mlb_reasons=sorted({str(s.get('reason')) for s in sources.values() if s.get('status')!='captured'}),
        kbo_states={d:{k:s.get(k) for k in ('status','reason')} for d,s in (state.get('daily_states') or {}).items()},
        kbo_error=state.get('reason'))
    db.put_document('baseball_candidate_watch',report)
    return report
