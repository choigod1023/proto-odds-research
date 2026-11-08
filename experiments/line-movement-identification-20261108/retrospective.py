"""Retrospective market-direction comparison; does not alter operations."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

KST = timezone(timedelta(hours=9))


def stamp(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return t.replace(tzinfo=KST) if t.tzinfo is None else t


def line_of(label, market):
    prefix = 'U' if market == '언더오버' else 'H' if market == '핸디캡' else None
    match = re.fullmatch(r'([UH])\s+([+-]?\d+(?:\.\d+)?)', label or '')
    if not match or match[1] != prefix:
        return None
    value = float(match[2])
    return value if value % 1 == .5 else None


def outcome(market, line, side, home, away):
    if market == '언더오버':
        return int((home + away < line) if side == 0 else (home + away > line))
    if market == '핸디캡':
        return int((home + line > away) if side == 0 else (home + line < away))
    raise ValueError('unsupported market')


def aggregate(rows):
    if not rows:
        return {'n': 0, 'old_hit_rate': None, 'new_hit_rate': None, 'delta_pp': None, 'old_roi': None, 'new_roi': None}
    n=len(rows); old=sum(r['new_product_old_hit'] for r in rows); new=sum(r['new_product_new_hit'] for r in rows)
    return {'n':n, 'old_hits':old, 'new_hits':new, 'old_hit_rate':old/n, 'new_hit_rate':new/n,
            'delta_pp':100*(new-old)/n, 'gain_hits':sum(not r['new_product_old_hit'] and r['new_product_new_hit'] for r in rows),
            'lost_hits':sum(r['new_product_old_hit'] and not r['new_product_new_hit'] for r in rows),
            'net_extra_hits':new-old,
            'old_roi':sum(r['new_product_old_hit']*r['new_odds'][r['old_side']]-1 for r in rows)/n,
            'new_roi':sum(r['new_product_new_hit']*r['new_odds'][r['new_side']]-1 for r in rows)/n,
            'original_ticket_hits':sum(r['original_ticket_hit'] for r in rows),
            'original_ticket_roi':sum(r['original_ticket_hit']*r['old_odds'][r['old_side']]-1 for r in rows)/n}


def analyze(history, response, db, score_history):
    games=defaultdict(dict)
    for section in ('live','past'):
        for game in response[section]:
            ident=tuple(game.get(k) for k in ('round','date','home','away','sport','league'))
            for opt in game.get('options',[]):
                if opt.get('게임번호') is not None:
                    games[(str(game['round']),str(opt['게임번호']))][ident]=game
    observations=defaultdict(list)
    for rnd, products in history['history'].items():
        for no, records in products.items():
            for r in records:
                observations[(str(rnd),str(no))].append((r['observed_at'],r.get('market'),r.get('label'),r.get('odds')))
    for state in db['states']:
        row=state['row']; key=(str(row['round']),str(row['game_no']))
        matched=games.get(key,{})
        if len(matched)!=1: continue
        game=next(iter(matched.values()))
        if any(row.get(k)!=game.get(k) for k in ('sport','league','home','away')) or row.get('date_text')!=game.get('date'):
            continue
        for obs in state['observations']:
            observations[key].append((obs['observed_at'],row['market_family'],row['market_label'],json.loads(row['odds_json'])))
    # Select the latest revision per original source/season/round/game_no;
    # retain historical identity only to recognize a later withdrawal.
    revisions=defaultdict(list)
    for score in score_history['score_rows']:
        if score.get('source')=='proto':
            revisions[(score['source'],score['season'],str(score['round']),str(score['game_no']))].append(score)
    physical=defaultdict(list)
    for versions in revisions.values():
        versions.sort(key=lambda x:stamp(x['observed_at']))
        identified=[x for x in versions if x.get('home_team') and x.get('away_team') and not str(x.get('kickoff','')).startswith('0001')]
        if not identified: continue
        identities={(x['sport'],x['league'],x['home_team'],x['away_team'],x['kickoff']) for x in identified}
        if len(identities)!=1: continue
        identity=next(iter(identities))
        physical[identity].append((versions[-1],identified[-1]))
    scores=defaultdict(list)
    for identity, versions in physical.items():
        sport,league,home,away,kickoff=identity
        for latest, known in versions:
            scores[(str(known['round']),sport,league,home,away)].append((latest,known,versions))
    out=[]; excluded=Counter()
    for key, records in observations.items():
        matches=games.get(key,{})
        if len(matches)!=1: excluded['ambiguous_game']+=1; continue
        game=next(iter(matches.values()))
        if game.get('sport') not in ('bk','bs'): continue
        scorekey=(key[0],game['sport'],game['league'],game['home'],game['away'])
        sr=scores.get(scorekey,[])
        if not sr: excluded['no_score_match']+=1; continue
        # Only one kickoff for this team pair in the round; latest revision wins.
        date=game.get('date','')
        sr=[x for x in sr if date.startswith(stamp(x[1]['kickoff']).strftime('%m.%d')) and date.endswith(stamp(x[1]['kickoff']).strftime('%H:%M'))]
        if len({x[1]['kickoff'] for x in sr})!=1: excluded['ambiguous_kickoff']+=1; continue
        latest, known, same_game = sr[0]
        # Across reissued rounds reject any withdrawn or inconsistent latest score.
        if any(x[0]['result_state']!='confirmed' for x in same_game): excluded['withdrawn_or_unconfirmed']+=1; continue
        if len({(x[0]['home_score'],x[0]['away_score']) for x in same_game})!=1: excluded['cross_round_score_conflict']+=1; continue
        kickoff=stamp(known['kickoff'])
        date=game.get('date','')
        if not date.startswith(kickoff.strftime('%m.%d')) or not date.endswith(kickoff.strftime('%H:%M')):
            excluded['date_mismatch']+=1; continue
        if any(stamp(x[0]['observed_at']) < kickoff for x in same_game): excluded['score_before_kickoff']+=1; continue
        # Require explicit product-level full-game settlement terms in the saved revision.
        record=game.get('prediction_record') or {}
        terms=[i.get('terms') or {} for i in (record.get('market_recommendations') or {}).get('items', [])
               if str(i.get('game_no'))==key[1]]
        if len(terms)!=1 or terms[0].get('period')!='full_including_extra' or terms[0].get('unit') not in ('points','runs'):
            excluded['missing_explicit_full_game_terms']+=1; continue
        snapshots=defaultdict(set)
        for at,market,label,odds in records:
            if market not in ('언더오버','핸디캡') or line_of(label,market) is None: continue
            if not isinstance(odds,list) or len(odds)!=2 or any(not isinstance(x,(int,float)) or x<=1 for x in odds) : continue
            t=stamp(at)
            if t<=kickoff-timedelta(minutes=30): snapshots[t].add((market,label,tuple(odds)))
        if len(snapshots)<2: excluded['fewer_than_two_pregame_quotes']+=1; continue
        if any(len(v)!=1 for v in snapshots.values()): excluded['conflicting_same_time']+=1; continue
        times=sorted(snapshots); first=next(iter(snapshots[times[0]])); last=next(iter(snapshots[times[-1]]))
        if first[0]!=last[0]: excluded['market_changed']+=1;continue
        if terms[0].get('market') != ('total' if first[0]=='언더오버' else 'handicap') or terms[0].get('settlement_rule') != ('total_half' if first[0]=='언더오버' else 'handicap_half') or terms[0].get('n_way') != 2:
            excluded['terms_mismatch']+=1; continue
        if first[2][0]==first[2][1] or last[2][0]==last[2][1]: excluded['tied_endpoint']+=1;continue
        if first[1:]==last[1:]: excluded['no_change_at_endpoints']+=1;continue
        oldside=int(first[2][1]<first[2][0]); newside=int(last[2][1]<last[2][0]); h=latest['home_score']; a=latest['away_score']
        if not isinstance(h,int) or not isinstance(a,int) or h<0 or a<0: excluded['invalid_score']+=1;continue
        oldline=line_of(first[1],first[0]); newline=line_of(last[1],last[0])
        out.append({'round':key[0],'game_no':key[1],'sport':game['sport'],'league':game['league'], 'home':game['home'],'away':game['away'], 'kickoff':kickoff.isoformat(),'score':[h,a], 'score_observed_at':latest['observed_at'], 'market':first[0], 'old_observed_at':times[0].isoformat(),'new_observed_at':times[-1].isoformat(), 'old_line':oldline,'new_line':newline,'old_odds':list(first[2]),'new_odds':list(last[2]),'old_side':oldside,'new_side':newside, 'new_product_old_hit':outcome(first[0],newline,oldside,h,a),'new_product_new_hit':outcome(first[0],newline,newside,h,a),'original_ticket_hit':outcome(first[0],oldline,oldside,h,a)})
    flips=[r for r in out if r['old_side']!=r['new_side']]
    return {'protocol':'earliest available quote vs latest available quote by T-30; lowest odds, no ties; select changed endpoints', 'exclusions':dict(excluded), 'all_changed':aggregate(out),'direction_flips':aggregate(flips),'flips_by_sport':{s:aggregate([r for r in flips if r['sport']==s]) for s in ('bs','bk')},'rows':out}


def read(p):
    if str(p).endswith('.gz'):
        with gzip.open(p,'rt',encoding='utf-8-sig') as f:return json.load(f)
    return json.loads(p.read_text(encoding='utf-8-sig'))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('history','response','db','scores','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(); result=analyze(read(args.history),read(args.response),read(args.db),read(args.scores))
    result['input_sha256']={n:hashlib.sha256(getattr(args,n).read_bytes()).hexdigest() for n in ('history','response','db','scores')}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
