import datetime as dt

def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00'))

def audit_pregame(feed, game):
    start=stamp(game['gameDate']); cutoff=start-dt.timedelta(minutes=35); reasons=[]
    if feed.get('gamePk')!=game['gamePk']: reasons.append('gamePk_mismatch')
    try:
        provider=dt.datetime.strptime(feed['metaData']['timeStamp'],'%Y%m%d_%H%M%S').replace(tzinfo=dt.timezone.utc)
        if provider>cutoff: reasons.append('provider_timestamp_after_cutoff')
    except (KeyError,ValueError,TypeError): reasons.append('provider_timestamp_missing_or_invalid')
    try:
        if stamp(feed['gameData']['datetime']['dateTime'])!=start: reasons.append('start_time_mismatch')
    except (KeyError,ValueError,TypeError): reasons.append('start_time_missing_or_invalid')
    for side in ('home','away'):
        expected=game.get('teams',{}).get(side,{}).get('team',{}).get('id')
        observed=feed.get('gameData',{}).get('teams',{}).get(side,{}).get('id')
        if expected is None or observed!=expected: reasons.append('team_identity_missing_or_mismatch')
    status=feed.get('gameData',{}).get('status',{}).get('abstractGameState')
    if status not in ('Preview','Pre-Game'): reasons.append('not_pregame')
    allplays=feed.get('liveData',{}).get('plays',{}).get('allPlays')
    if not isinstance(allplays,list): reasons.append('allPlays_missing_or_invalid'); allplays=[]
    for play in allplays:
        times=[play.get('about',{}).get(k) for k in ('startTime','endTime')]
        for event in play.get('playEvents',[]):
            times.extend(event.get(k) for k in ('startTime','endTime'))
            if event.get('isPitch'): reasons.append('pitch_in_pregame')
        for t in times:
            if t:
                try:
                    if stamp(t)>cutoff: reasons.append('play_after_cutoff')
                except (ValueError,TypeError): reasons.append('invalid_play_time')
    teams=feed.get('liveData',{}).get('boxscore',{}).get('teams',{})
    lineups={}
    for side in ('home','away'):
        team=teams.get(side,{})
        lineup=team.get('battingOrder',[])
        players=team.get('players',{})
        initial=[]
        for x in players.values():
            code=x.get('battingOrder')
            if isinstance(code,str) and code in {str(i*100) for i in range(1,10)}: initial.append((int(code),x.get('person',{}).get('id')))
        initial=[x[1] for x in sorted(initial)]
        lineups[side]={'battingOrder':lineup,'starting_codes_100_to_900':initial,'both_nine_unique':len(lineup)==9 and len(set(lineup))==9 and len(initial)==9 and len(set(initial))==9,'arrays_match':lineup==initial}
        bat=team.get('teamStats',{}).get('batting',{}); pit=team.get('teamStats',{}).get('pitching',{})
        if any(bat.get(k,0) not in (0,None) for k in ('atBats','plateAppearances','hits','runs')) or any(pit.get(k,0) not in (0,None) for k in ('numberOfPitches','outs','battersFaced')): reasons.append('target_game_stats_nonzero')
    return {'event_replay_checks_pass':not reasons,'reasons':sorted(set(reasons)),'lineups':lineups,'strict_pit_certified':False,'lineup_publication_asof_verified':False}
