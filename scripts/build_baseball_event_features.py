"""Pregame recent form and past relief workload, with explicit temporal exclusions."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse, gzip, json, math

from collect_baseball_event_history import read, stamp

UTC=timezone.utc


def completion_bound(game):
    # Do not use schedule's final score/status to manufacture an announcement.
    if game.get('status',{}).get('codedGameState')!='F': return None
    if any(game.get(k) for k in ['resumeDate','resumedFrom']): return None
    info=game.get('gameInfo',{})
    if not info.get('firstPitch') or not info.get('gameDurationMinutes'): return None
    return stamp(info['firstPitch'])+timedelta(minutes=float(info['gameDurationMinutes'])+float(info.get('delayDurationMinutes',0))+60)


def eligible_logs(rows, cutoff, days):
    low=cutoff-timedelta(days=days)
    return [r for r in rows if low<=r['played_at'] and r['completed_bound']<cutoff]


def total(rows, key): return sum(float(r['stat'].get(key,0)) for r in rows)


def batting_rate(rows, prior_obp=.32, prior_slg=.41, shrink=60):
    ab=total(rows,'atBats');bb=total(rows,'baseOnBalls');hp=total(rows,'hitByPitch');sf=total(rows,'sacFlies');pa=ab+bb+hp+sf
    return dict(obp=(total(rows,'hits')+bb+hp+shrink*prior_obp)/(pa+shrink),
        slg=(total(rows,'totalBases')+shrink*prior_slg)/(ab+shrink),
        kr=(total(rows,'strikeOuts')+shrink*.22)/(pa+shrink),pa=pa)


def features(snapshot, player, team, games, announcements):
    start=stamp(snapshot['start_at']);cutoff=start-timedelta(minutes=10);current=games[snapshot['gamePk']]
    official_day=current['officialDate'];day=datetime.fromisoformat(official_day).date()
    sides=[];proof={};changes={};used=set()
    latest=announcements.get((snapshot['gamePk'],10))
    if latest and (not latest.get('accepted') or not all(s['complete_lineup'] and s['starter'] and s.get('starter_kind')=='pregame_box_listed' for s in latest['sides'].values())): latest=None
    if latest is None: raise ValueError('no_complete_pregame_box_listed_starter_and_lineup')
    for side in ['home','away']:
        tid=current['teams'][side]['team']['id']
        ids=latest['sides'][side]['lineup'];starter=latest['sides'][side]['starter']
        observed=stamp(latest['observed_at'])
        if observed>cutoff or observed>=start: raise ValueError('current_announcement_after_cutoff')
        bat=[];side_used=[]
        for pid in ids:
            history=[r for r in player.get((pid,'hitting'),[]) if r['completed_bound']<cutoff and r['date'][:4]==official_day[:4]]
            season=batting_rate(history,shrink=150)
            recent14=eligible_logs(history,cutoff,14);recent30=eligible_logs(history,cutoff,30)
            r14=batting_rate(recent14,season['obp'],season['slg'],60);r30=batting_rate(recent30,season['obp'],season['slg'],120)
            bat.append(dict(obp14=r14['obp'],slg14=r14['slg'],k14=r14['kr'],obp30=r30['obp'],slg30=r30['slg'],
                obp_change=r14['obp']-season['obp'],slg_change=r14['slg']-season['slg'],pa14=r14['pa']))
            side_used.extend(history)
        pitch_history=[r for r in player.get((starter,'pitching'),[]) if r['completed_bound']<cutoff and r['date'][:4]==official_day[:4]]
        starts=[r for r in pitch_history if r['stat'].get('gamesStarted')==1]
        p30=eligible_logs(starts,cutoff,30);side_used.extend(pitch_history)
        outs=total(p30,'outs');bf=total(p30,'battersFaced')
        rest=min(14,(day-max(datetime.fromisoformat(r['date']).date() for r in starts)).days) if starts else 14
        prior_relief=[r for r in team.get((tid,'pitching'),[]) if r['stat'].get('gamesStarted')==0 and r['date']<official_day]
        relief=eligible_logs(prior_relief,cutoff,28);side_used.extend(relief)
        dates=[(day-timedelta(days=i)).isoformat() for i in [1,2,3]]
        yesterday=[r for r in relief if r['date']==dates[0]]
        d3=[r for r in relief if r['date'] in dates]
        yesterday_ids={r['player'] for r in yesterday};two_days_ids={r['player'] for r in relief if r['date']==dates[1]}
        roles=defaultdict(lambda:[0.,0.])
        for r in relief:
            roles[r['player']][0]+=float(r['stat'].get('saves',0))+float(r['stat'].get('holds',0))
            roles[r['player']][1]+=float(r['stat'].get('outs',0))
        top=sorted(roles,key=lambda pid:(-roles[pid][0],-roles[pid][1],pid))[:3]
        team_bat=eligible_logs(team.get((tid,'hitting'),[]),cutoff,14);side_used.extend(team_bat)
        # Sum player appearances for team hitting; no future roster membership is a feature.
        bteam=batting_rate(team_bat,shrink=150)
        team_pitch=eligible_logs(team.get((tid,'pitching'),[]),cutoff,14);side_used.extend(team_pitch)
        txouts=total(team_pitch,'outs')
        value={k:sum(b[k] for b in bat)/9 for k in ['obp14','slg14','k14','obp30','slg30','obp_change','slg_change']}
        value.update(starter_era30=(27*total(p30,'earnedRuns')+150*4.3)/(outs+150),
            starter_kbb30=(total(p30,'strikeOuts')-total(p30,'baseOnBalls')+100*.14)/(bf+100),
            starter_rest=rest,team_obp14=bteam['obp'],team_slg14=bteam['slg'],
            team_ra14=(27*total(team_pitch,'runs')+150*4.3)/(txouts+150),
            relief_pitches1=total(yesterday,'numberOfPitches'),relief_pitches3=total(d3,'numberOfPitches'),
            relief_consecutive=len(yesterday_ids&two_days_ids),relief_top3_used1=sum(pid in yesterday_ids for pid in top),
            relief_kbb28=(total(relief,'strikeOuts')-total(relief,'baseOnBalls')+100*.14)/(total(relief,'battersFaced')+100))
        earliest=next((announcements.get((snapshot['gamePk'],m)) for m in [120,30]
                       if announcements.get((snapshot['gamePk'],m),{}).get('accepted') and announcements[(snapshot['gamePk'],m)]['sides'][side]['complete_lineup']),None)
        replaced=0;strength=0.;changed_starter=0
        if earliest:
            old=earliest['sides'][side]
            replaced=len(set(ids)-set(old['lineup']))
            def rate(pid):return batting_rate(eligible_logs(player.get((pid,'hitting'),[]),cutoff,30),shrink=120)['obp']
            strength=sum(rate(pid) for pid in ids)/9-sum(rate(pid) for pid in old['lineup'])/9
            changed_starter=int(bool(old['starter']) and old['starter']!=starter)
        value.update(lineup_replacements=replaced,lineup_strength_revision=strength,probable_starter_change=changed_starter)
        sides.append(value)
        unique={r['gamePk'] for r in side_used};used.update(unique)
        proof[side]=dict(team=tid,starter=starter,lineup=ids,lineup_recent14_with_10pa=sum(b['pa14']>=10 for b in bat),
            relief_appearances28=len(relief),relief_pool_recent28=sorted(roles),top3_past_save_hold_ids=top,
            latest_completion_bound=max((r['completed_bound'] for r in side_used),default=None).isoformat() if side_used else None,
            source_game_ids=sorted(unique),announcement_at=observed.isoformat())
        changes[side]=dict(earlier_complete_observed=earliest.get('observed_at') if earliest else None,
                          replaced=replaced,probable_starter_changed=changed_starter)
    return {k:sides[0][k]-sides[1][k] for k in sides[0]},dict(cutoff_at=cutoff.isoformat(),sides=proof,changes=changes,source_games=sorted(used))


def build(source, snapshots, previous, output):
    games={};sources=[]
    for path in sorted((source/'sources').glob('schedule-*.gz')):
        j=read(path)[0];games.update({g['gamePk']:g for g in j['games']});sources.append({k:j[k] for k in ['url','response_sha256','collected_at']})
    player=defaultdict(list);team=defaultdict(list);unique={};excluded=Counter()
    repairs=sorted((source/'sources').glob('repair-*.gz'))
    repaired_ids={read(p)[0]['gamePk'] for p in repairs}
    for path in sorted((source/'sources').glob('logs-*.gz'))+repairs:
        j=read(path)[0]
        if not j['accepted']: excluded['failed_team_season']+=1;continue
        sources.append({k:j[k] for k in ['url','response_sha256','collected_at']})
        for r in j['rows']:
            if not path.name.startswith('repair-') and r['gamePk'] in repaired_ids:
                excluded['replaced_by_official_box_repair']+=1;continue
            key=(r['gamePk'],r['player'],r['kind']);g=games.get(r['gamePk'])
            bound=completion_bound(g) if g else None
            if bound is None: excluded['no_safe_completion_bound']+=1;continue
            if key in unique:
                if unique[key]['stat']!=r['stat']:raise ValueError('conflicting_player_log')
                excluded['duplicate_player_log']+=1;continue
            r=dict(r,played_at=stamp(g['gameInfo']['firstPitch']),completed_bound=bound)
            unique[key]=r;player[(r['player'],r['kind'])].append(r);team[(r['team'],r['kind'])].append(r)
    announcements={(r['gamePk'],r['minutes_before']):r for r in read(source/'announcements.jsonl.gz')} if (source/'announcements.jsonl.gz').exists() else {}
    for p in sorted((source/'sources').glob('announcement-*.gz')):
        r=read(p)[0];announcements[(r['gamePk'],r['minutes_before'])]=r
    for p in sorted((source/'sources').glob('confirmation-*.gz')):
        r=read(p)[0];announcements[(r['gamePk'],r['minutes_before'])]=r
    old={r['gamePk']:r for r in read(previous or source/'cohort-inputs.jsonl')};rows=[];cohort=[]
    for s in read(snapshots) if snapshots else old.values():
        if not s.get('accepted',True) or s['gamePk'] not in old: continue
        original=old[s['gamePk']]
        if not (s['role']=='evaluation' or (s['role']=='train' and 'historical_prices' in original)):continue
        if s['gamePk'] not in games:excluded['target_schedule_missing']+=1;continue
        cohort.append({k:original[k] for k in ['gamePk','role','date','start_at','hg','ag','historical_prices','target'] if k in original})
        try:x,proof=features(s,player,team,games,announcements)
        except ValueError as e:excluded[str(e)]+=1;continue
        rows.append(dict(gamePk=s['gamePk'],role=s['role'],date=original['date'],start_at=s['start_at'],hg=original['hg'],ag=original['ag'],
            historical_prices=original.get('historical_prices'),target=original.get('target'),features=x,proof=proof))
    output.mkdir(parents=True,exist_ok=True)
    (output/'cohort-inputs.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in cohort),encoding='utf8',newline='\n')
    (output/'event-features.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8',newline='\n')
    summary=dict(rows=len(rows),roles=dict(Counter(r['role'] for r in rows)),eligible_player_game_groups=len(unique),
        excluded=dict(excluded),source_requests=sources,
        announcements=dict(Counter(f"{r['minutes_before']}:{'accepted' if r['accepted'] else r.get('reason','failed')}" for r in announcements.values())),
        changed_games=sum(any(c['replaced'] or c['probable_starter_changed'] for c in r['proof']['changes'].values()) for r in rows))
    (output/'feature-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    with gzip.open(output/'announcements.jsonl.gz','wt',encoding='utf8',newline='\n') as f:
        for r in sorted(announcements.values(),key=lambda r:(r['gamePk'],r['minutes_before'])):f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='source_requests'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source','output']:p.add_argument('--'+n,type=Path,required=True)
    for n in ['snapshots','previous']:p.add_argument('--'+n,type=Path)
    a=p.parse_args();build(a.source,a.snapshots,a.previous,a.output)
