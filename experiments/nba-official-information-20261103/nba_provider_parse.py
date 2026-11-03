import datetime, json, gzip, hashlib
from collections import Counter,defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

def checked_body(row, directory):
    packed=(directory/row['file']).read_bytes()
    if hashlib.sha256(packed).hexdigest()!=row['stored_sha256']:raise ValueError('stored hash mismatch')
    body=gzip.decompress(packed)
    if hashlib.sha256(body).hexdigest()!=row['body_sha256']:raise ValueError('body hash mismatch')
    return body

def build_census(records,directory,dates):
    byname={r['name']:r for r in records}; month_dates=defaultdict(list)
    for date in dates:month_dates[date[:7]].append(date)
    selected=[]; months={};seen=set()
    for month,days in sorted(month_dates.items()):
        rows=[]; failures=[]; other=0
        for day in days:
            rec=byname.get('scoreboard-'+day)
            if not rec or rec.get('status')!=200:
                failures.append({'day':day,'reason':'missing_or_failed'});continue
            try:
                doc=json.loads(checked_body(rec,directory));events=doc['events']
                if not isinstance(events,list) or len(events)>=100:raise ValueError('incomplete_or_invalid_event_list')
                for ev in events:
                    season=ev['season']; typ=season['type']; year=season['year']
                    if typ!=2 or year!=2026:other+=1;continue
                    gid=ev['id']; date=ev['date']; comps=ev['competitions']
                    if len(comps)!=1: raise ValueError('competition_count')
                    if comps[0].get('type',{}).get('abbreviation')=='ALLSTAR':other+=1;continue
                    if comps[0].get('type',{}).get('abbreviation')!='STD':raise ValueError('unknown_competition_type')
                    if not gid.isdigit() or len(comps)!=1 or comps[0]['id']!=gid:raise ValueError('identity')
                    actual=datetime.datetime.fromisoformat(date.replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                    if actual!=day:raise ValueError('fixture_day_mismatch')
                    teams=comps[0]['competitors']
                    if len(teams)!=2 or {x['homeAway'] for x in teams}!={'home','away'}:raise ValueError('teams')
                    byside={x['homeAway']:x['team'] for x in teams}
                    rows.append({'provider':'espn','provider_game_id':gid,'game_day_et':day,'tipoff_utc':date,'home_team_id':byside['home']['id'],'away_team_id':byside['away']['id'],'home_team':byside['home']['displayName'],'away_team':byside['away']['displayName'],'scoreboard_file':rec['file']})
            except (KeyError,ValueError,TypeError) as e:failures.append({'day':day,'reason':str(e)})
        ids=[x['provider_game_id'] for x in rows]
        if len(ids)!=len(set(ids)) or seen.intersection(ids):failures.append({'day':None,'reason':'duplicate_game_id'})
        if failures:
            months[month]={'status':'blocked','dates':len(days),'failures':failures,'regular_identity_count':None,'selected_count':0,'other_season_or_type_events':other};continue
        seen.update(ids);rows.sort(key=lambda x:(x['game_day_et'],x['provider_game_id']))
        chosen=rows[:18];selected.extend(chosen)
        months[month]={'status':'complete','dates':len(days),'regular_identity_count':len(rows),'selected_count':len(chosen),'unselected_count':len(rows)-len(chosen),'other_season_or_type_events':other}
    return {'provider':'ESPN','season':'2025-26','selection':'date ET then ESPN game ID, first 18 each complete month','months':months,'selected':selected}
