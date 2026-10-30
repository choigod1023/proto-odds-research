"""Bounded research-only MLB bullpen extraction; no outcomes/model fitting.
Feature definitions frozen before extraction: prior 48/72h relief pitches,
relievers with >=30 pitches/72h, relievers used on >=2 distinct prior dates.
No roster membership/health or confirmed availability is inferred.
"""
import argparse
import collections
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import urllib.request

UTC = timezone.utc

def dt(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('aware timestamp required')
    return result

def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=True, indent=2), encoding='utf8')

def write_rows(path, rows):
    with gzip.open(path, 'wt', encoding='utf8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=True) + '\n')

def complete_lineup(players):
    """Only nine unique starters, exact batting slots; substitutes not starters."""
    slots = [str(p.get('battingOrder', '')) for p in players]
    return len(players) == 9 and len({p['id'] for p in players}) == 9 and set(slots) == {str(i*100) for i in range(1,10)}

def prior_relief(box, game, target_id, decision):
    """Require exact official end timestamp, never estimate game completion."""
    end = game.get('gameInfo', {}).get('lastPitch')
    # Schedule gameInfo does not reliably supply lastPitch: caller hydrates linescore.
    end = game.get('end_time', end)
    if not end or dt(end) >= decision or str(game['gamePk']) == str(target_id):
        return []
    rows = []
    for side in ('home', 'away'):
        team = box['teams'][side]
        for player in team.get('players', {}).values():
            stats = player.get('stats', {}).get('pitching', {})
            if not stats:
                continue
            if 'gamesStarted' not in stats:
                raise ValueError('pitcher_role_missing')
            if stats['gamesStarted'] != 0:
                continue
            pitches = stats.get('numberOfPitches', stats.get('pitchesThrown'))
            if pitches is None:
                raise ValueError('relief_pitch_count_missing')
            rows.append(dict(team_id=team['team']['id'], player_id=player['person']['id'],
                             pitches=pitches, end_time=end, game_id=game['gamePk'],
                             source_sha256=game['source_sha256'], observed_at=game['observed_at']))
    return rows

def workload(records, team_id, decision, target_id):
    valid = [r for r in records if r['team_id'] == team_id and str(r['game_id']) != str(target_id)
             and decision-timedelta(hours=72) <= dt(r['end_time']) < decision]
    # Every game/player observation contributes once, including doubleheaders.
    valid = list({(r['game_id'], r['player_id']): r for r in valid}.values())
    pitches = collections.Counter()
    days = collections.defaultdict(set)
    for r in valid:
        pitches[r['player_id']] += r['pitches']
        days[r['player_id']].add(dt(r['end_time']).date())
    return dict(relief_pitches_48h=sum(r['pitches'] for r in valid if dt(r['end_time']) >= decision-timedelta(hours=48)),
                relief_pitches_72h=sum(pitches.values()),
                relievers_ge30_pitches_72h=sum(v >= 30 for v in pitches.values()),
                relievers_used_ge2_dates_72h=sum(len(v) >= 2 for v in days.values())), valid

class Fetch:
    def __init__(self, root, limit):
        self.root=root; self.limit=min(limit,50); self.count=0; self.manifest=[]
        root.mkdir(parents=True, exist_ok=True)
        self.initial_cached=len(list(root.glob('*.meta.json')))
    def get(self, suffix):
        url='https://statsapi.mlb.com'+suffix
        key=hashlib.sha256(url.encode()).hexdigest()
        cache=self.root/(key+'.json.gz')
        meta=self.root/(key+'.meta.json')
        if cache.exists() and meta.exists():
            raw=gzip.decompress(cache.read_bytes()); info=json.loads(meta.read_text())
        else:
            if self.initial_cached + self.count >= self.limit: raise RuntimeError('request_budget_exhausted')
            self.count+=1
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'pregame-research/1.0'}),timeout=20) as response:
                raw=response.read(12_000_001)
            if len(raw)>12_000_000: raise ValueError('payload_limit')
            info=dict(url=url, observed_at=datetime.now(UTC).isoformat(),sha256=hashlib.sha256(raw).hexdigest(),path=str(cache))
            cache.write_bytes(gzip.compress(raw)); dump(meta,info)
        self.manifest.append(info)
        return json.loads(raw),info

def extract(args):
    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    fetch=Fetch(out/'raw',args.max_requests)
    frames=[]; inputs=[]
    for roster in args.roster:
        path=Path(roster); inputs.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        with gzip.open(path,'rt',encoding='utf8') as f:
            for line in f:
                r=json.loads(line)
                if r['league'] in ('KBO','NPB','MLB'):
                    frames.append({k:r[k] for k in ('cohort','event_id','league','kickoff','available_at') if k in r})
    # Freeze before any requests; never read target labels, prices or results.
    dump(out/'protocol.json',dict(features=['relief_pitches_48h','relief_pitches_72h','relievers_ge30_pitches_72h','relievers_used_ge2_dates_72h'],
         max_events=args.max_events, max_requests=fetch.limit, choice='earliest distinct MLB roster events',
         tier='retrospective_lagged', target_lineups=False, roi_inspected=False))
    prepared={}; audit=[]
    selected=sorted({r['event_id']:r for r in frames if r['league']=='MLB'}.values(),key=lambda r:r['kickoff'])[:args.max_events]
    selected_ids={r['event_id'] for r in selected}
    candidates=list({(r['event_id'],r.get('available_at')):r for r in frames if r['event_id'] in selected_ids}.values())
    for r in candidates:
        event=r['event_id']; gid=event.split(':')[-1]
        decision=dt(r.get('available_at') or r['kickoff']) if r.get('available_at') else dt(r['kickoff'])-timedelta(minutes=30)
        try:
            target,tm=fetch.get('/api/v1/schedule?gamePk='+gid)
            games=[g for d in target.get('dates',[]) for g in d['games']]
            if len(games)!=1 or str(games[0]['gamePk'])!=gid: raise ValueError('target_identity_missing')
            t=games[0]
            if abs((dt(t['gameDate'])-dt(r['kickoff'])).total_seconds())>1800: raise ValueError('kickoff_mismatch')
            team_ids=[t['teams'][s]['team']['id'] for s in ('home','away')]
            start=(decision-timedelta(days=4)).date(); end=decision.date()
            schedule,sm=fetch.get(f'/api/v1/schedule?sportId=1&teamId={team_ids[0]},{team_ids[1]}&startDate={start}&endDate={end}')
            records=[]; complete=True; ids=[tm['sha256'],sm['sha256']]
            prior=[g for d in schedule.get('dates',[]) for g in d['games'] if str(g['gamePk'])!=gid and dt(g['gameDate'])<decision and dt(g['gameDate'])>=decision-timedelta(days=4)]
            for g in prior:
                if g.get('gameType') not in ('R','F','D','L','W'): continue
                if g['status'].get('abstractGameState')!='Final': continue
                # Official live feed has timestamped last play; no inferred end time.
                feed,fm=fetch.get(f"/api/v1.1/game/{g['gamePk']}/feed/live")
                ids.append(fm['sha256'])
                plays=feed.get('liveData',{}).get('plays',{}).get('allPlays',[])
                ends=[p.get('about',{}).get('endTime') for p in plays if p.get('about',{}).get('endTime')]
                if not ends: complete=False; continue
                g=dict(g,end_time=max(ends,key=dt),source_sha256=fm['sha256'],observed_at=fm['observed_at'])
                records.extend(prior_relief(feed['liveData']['boxscore'],g,gid,decision))
            if not complete: raise ValueError('prior_game_end_missing')
            features={}; evidence={}
            for side,team in zip(('home','away'),team_ids):
                values,used=workload(records,team,decision,gid)
                # A zero requires all scheduled relevant games inspected.
                for name,value in values.items():
                    key=side+'_'+name; features[key]=value
                    evidence[key]=dict(max_fact_time=max((x['end_time'] for x in used),key=dt,default=None),
                        observed_at=max((m['observed_at'] for m in fetch.manifest if m['sha256'] in ids),default=None),
                        source_sha256=sorted(set(ids)),availability_tier='retrospective_lagged',
                        note='Official historical final play cutoff; no authenticated pregame capture. Regular/postseason relief use proxy, not current bullpen roster/health; no target lineup or season totals.')
            prepared[(event,decision.isoformat())]=(features,evidence,sorted(set(ids)))
        except Exception as exc:
            audit.append(dict(event_id=event,reason=type(exc).__name__+':'+str(exc)))
    rows=[]
    for r in frames:
        decision=dt(r['available_at']) if r.get('available_at') else dt(r['kickoff'])-timedelta(minutes=30)
        found=prepared.get((r['event_id'],decision.isoformat()))
        if found:
            f,e,s=found; rows.append(dict(cohort=r['cohort'],event_id=r['event_id'],league=r['league'],kickoff=r['kickoff'],decision_at=decision.isoformat(),features=f,feature_evidence=e,source_ids=s))
        else:
            audit.append(dict(cohort=r['cohort'],event_id=r['event_id'],league=r['league'],reason='no_verified_local_pitch_logs' if r['league']!='MLB' else 'not_extracted_or_request_budget'))
    write_rows(out/'features.jsonl.gz',rows); write_rows(out/'audit.jsonl.gz',audit)
    dump(out/'source-manifest.json',dict(inputs=inputs,sources=fetch.manifest,network_requests_this_run=fetch.count,total_cached_sources=len(list(fetch.root.glob('*.meta.json')))))
    dump(out/'summary.json',dict(frame_rows=len(frames), feature_rows=len(rows),unique_events=len({r['event_id'] for r in rows}),
         by_league=dict(collections.Counter(r['league'] for r in frames)),missing_reasons=dict(collections.Counter(r['reason'] for r in audit)),evidence_tier='retrospective_lagged'))
    print(json.dumps(json.loads((out/'summary.json').read_text())))

def capture(args):
    """Bounded local forward intake. Only future official fixtures are retained."""
    out=Path(args.output); fetch=Fetch(out/'forward-raw',args.max_requests)
    now=datetime.now(UTC)
    schedule,meta=fetch.get(f'/api/v1/schedule?sportId=1&startDate={now.date()}&endDate={(now+timedelta(days=7)).date()}&hydrate=probablePitcher')
    rows=[]
    for d in schedule.get('dates',[]):
        for g in d['games']:
            if dt(g['gameDate'])<=dt(meta['observed_at']): continue
            rows.append(dict(game_id=g['gamePk'],kickoff=g['gameDate'],observed_at=meta['observed_at'],source_sha256=meta['sha256'],
                teams=g['teams'],note='Forward captured probable pitchers only; not confirmed lineup.'))
    write_rows(out/'forward-fixtures.jsonl.gz',rows); dump(out/'forward-manifest.json',fetch.manifest)
    print('forward fixtures:',len(rows))


def calendar_workload(records, cutoff):
    """Date-only structural lag: preserve unknown max_fact_time, never invent end."""
    low=(cutoff-timedelta(days=4)).date().isoformat()
    high=(cutoff-timedelta(days=2)).date().isoformat()
    chosen=[r for r in records if low <= r['date'] <= high]
    chosen=list({(r['gamePk'],r['player']):r for r in chosen}.values())
    by_player=collections.Counter()
    for r in chosen:
        if r['pitches'] is None: raise ValueError('missing_pitch_count')
        by_player[r['player']]+=r['pitches']
    return sum(v>=30 for v in by_player.values()),chosen


def auxiliary(args):
    """Read-only existing game logs; date precision explicitly remains unverified."""
    import ast
    source=Path(args.local_history); out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    dump(out/'calendar-protocol.json',dict(feature='side_relievers_ge30_pitches_lagged_dates_2_4',feature_count=2,dates='UTC decision date minus 4 through minus 2 inclusive',missing_end_timestamp=None,tier='retrospective_lagged',roi_inspected=False,missing_history='omit row',repairs='official cached box repair replaces full game logs'))
    # Read the existing explicit alias literal without importing model code.
    tree=ast.parse((Path(__file__).parents[1]/'src/outcome_signal_backtest_v2.py').read_text(encoding='utf8'))
    aliases=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEAM_ALIASES' for t in n.targets))
    games={}; refs=[]; logs=collections.defaultdict(list); coverage=set(); selected_raw={}
    def read(path):
        return json.loads(next(gzip.open(path,'rt',encoding='utf8')))
    for path in sorted(source.glob('schedule-*.gz')):
        j=read(path); refs.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),response_sha256=j['response_sha256'],observed_at=j['collected_at']))
        for g in j['games']:
            games[g['gamePk']]=g
    repairs=sorted(source.glob('repair-*.gz'))
    repaired_ids={read(p)['gamePk'] for p in repairs if read(p).get('accepted')}
    for path in sorted(source.glob('logs-*.gz'))+repairs:
        j=read(path)
        if not j.get('accepted'): continue
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        refs.append(dict(path=str(path),sha256=sha,response_sha256=j['response_sha256'],observed_at=j['collected_at']))
        if 'team' in j: coverage.add((j['team'],str(j['year'])))
        for r in j['rows']:
            if not path.name.startswith('repair-') and r['gamePk'] in repaired_ids: continue
            if r['kind']!='pitching' or r['stat'].get('gamesStarted')!=0: continue
            if r['gamePk'] not in games or games[r['gamePk']].get('gameType') not in ('R','F','D','L','W'): continue
            if any(games[r['gamePk']].get(k) for k in ('resumeDate','resumedFrom')): continue
            logs[r['team']].append(dict(gamePk=r['gamePk'],player=r['player'],date=r['date'],
                 pitches=r['stat'].get('numberOfPitches'),source_sha256=sha,observed_at=j['collected_at']))
    lookup=collections.defaultdict(list)
    for g in games.values():
        names=[g['teams'][s]['team']['name'] for s in ('home','away')]
        names=[n.replace('St. Louis','St.Louis') for n in names]
        short=tuple(aliases.get(('MLB',n),n) for n in names)
        lookup[short].append(g)
    rows=[]; joins=[]; audits=collections.Counter(); inputs=[]; frame_seen=set()
    for file in [args.archive]+args.roster:
        path=Path(file); inputs.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for raw in gzip.open(path,'rt',encoding='utf8'):
            r=json.loads(raw)
            if r['league']!='MLB': continue
            cutoff=dt(r['available_at']) if r.get('available_at') else dt(r['kickoff'])-timedelta(minutes=30)
            key=(r['cohort'],r['event_id'],cutoff.isoformat())
            if key in frame_seen: continue
            frame_seen.add(key)
            if str(r['event_id']).startswith('MLB:'):
                matches=[games[int(r['event_id'].split(':')[1])]] if int(r['event_id'].split(':')[1]) in games else []
            else:
                names=tuple(aliases.get(('MLB',r[s]),r[s]) for s in ('home','away'))
                matches=[g for g in lookup.get(names,[]) if abs((dt(g['gameDate'])-dt(r['kickoff'])).total_seconds())<=1800]
            if len(matches)!=1:
                audits['target_schedule_missing_or_ambiguous']+=1;continue
            g=matches[0]
            if cutoff>=dt(r['kickoff']) or abs((dt(g['gameDate'])-dt(r['kickoff'])).total_seconds())>1800:
                audits['cutoff_or_kickoff_invalid']+=1;continue
            features={}; evidence={}; source_ids=set(); failed=False
            for side in ('home','away'):
                tid=g['teams'][side]['team']['id']
                if (tid,cutoff.strftime('%Y')) not in coverage:
                    failed=True;audits['team_season_not_covered']+=1;break
                eligible=[x for x in logs[tid] if x['gamePk']!=g['gamePk']]
                try: value,used=calendar_workload(eligible,cutoff)
                except ValueError:
                    failed=True;audits['missing_pitch_count']+=1;break
                # Missing workload history is missing, not evidence for a rested bullpen.
                if not used:
                    failed=True;audits['no_observed_relief_in_window']+=1;break
                hashes=sorted({x['source_sha256'] for x in used});source_ids.update(hashes)
                name=side+'_relievers_ge30_pitches_lagged_dates_2_4'
                features[name]=value
                evidence[name]=dict(max_fact_time=None,observed_at=max(x['observed_at'] for x in used),source_sha256=hashes,
                   availability_tier='retrospective_lagged',note='STRUCTURAL DATE LAG ONLY. Actual per-game end timestamp unavailable; max_fact_time intentionally null. Uses pitching game logs on UTC decision dates minus 2 through 4; no target lineup, current roster, health or confirmed availability. Parent must not treat as authenticated as-of evidence.')
                for x in used: selected_raw[(x['gamePk'],x['player'],tid)]=dict(x,team_id=tid)
            if failed: continue
            joins.append(dict(cohort=r['cohort'],event_id=r['event_id'],decision_at=cutoff.isoformat(),gamePk=g['gamePk'],teams={side:g['teams'][side]['team']['id'] for side in ('home','away')}))
            rows.append(dict(cohort=r['cohort'],event_id=r['event_id'],league=r['league'],kickoff=r['kickoff'],decision_at=cutoff.isoformat(),
                features=features,feature_evidence=evidence,source_ids=sorted(source_ids)))
    write_rows(out/'calendar-features.jsonl.gz',rows)
    write_rows(out/'calendar-joins.jsonl.gz',joins)
    write_rows(out/'calendar-selected-inputs.jsonl.gz',selected_raw.values())
    dump(out/'calendar-source-manifest.json',dict(inputs=inputs,sources=refs,join='explicit team aliases + kickoff within 30 minutes, unique match; exact MLB id for tipster frames',raw_record_key=['gamePk','player','team_id']))
    summary=dict(rows=len(rows),by_cohort=dict(collections.Counter(r['cohort'] for r in rows)),missing=dict(audits),
                 unknown_max_fact_time=True,selected_raw_records=len(selected_raw),new_features=2)
    dump(out/'calendar-summary.json',summary);print(json.dumps(summary))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--roster',action='append',default=[]); p.add_argument('--output',required=True)
    p.add_argument('--max-events',type=int,default=5); p.add_argument('--max-requests',type=int,default=48); p.add_argument('--capture-forward',action='store_true'); p.add_argument('--archive'); p.add_argument('--local-history')
    a=p.parse_args(); auxiliary(a) if a.archive else capture(a) if a.capture_forward else extract(a)
