import collections, gzip, hashlib, json
from pathlib import Path
import collect
from audit import audit_pregame
P=Path(__file__).resolve().parent; E=P/'evidence'
def read(name):
    body=E/(name+'.json.gz'); meta=E/(name+'.meta.json')
    if not body.exists() or not meta.exists(): return None,None
    raw=gzip.decompress(body.read_bytes()); m=json.loads(meta.read_text(encoding='utf8'))
    if hashlib.sha256(raw).hexdigest()!=m['sha256'] or len(raw)!=m['bytes'] or m['status']!=200: raise ValueError('invalid evidence '+name)
    return json.loads(raw),m

def main():
    schedule,sm=read('schedule-2026'); rows=[x for d in schedule['dates'] for x in d['games']]; counts=collections.Counter(x['gamePk'] for x in rows)
    games=collect.games(schedule); chosen=collect.selected(games); results=[]; coverage=collections.Counter()
    for g in chosen:
        ident=g['gamePk']; pre,pm=read(f'{ident}-pregame'); post,bm=read(f'{ident}-boxscore'); coverage['selected']+=1
        row={'gamePk':ident,'gameDate':g['gameDate'],'officialDate':g['officialDate'],'home':g['teams']['home']['team'],'away':g['teams']['away']['team'],'duplicate_schedule_rows':counts[ident], 'strict_pit_certified':False,'pregame':None,'postgame':None}
        if pre:
            coverage['pregame_raw']+=1; audit=audit_pregame(pre,g)
            if counts[ident]>1: audit['event_replay_checks_pass']=False; audit['reasons'].append('ambiguous_schedule_versions')
            coverage['event_replay_pass']+=int(audit['event_replay_checks_pass'])
            teams=audit['lineups']; coverage['both_lineups_nine_and_code_match']+=int(all(teams[s]['both_nine_unique'] and teams[s]['arrays_match'] for s in ('home','away')))
            pp=pre.get('gameData',{}).get('probablePitchers',{}); coverage['both_probables']+=int(all(pp.get(s,{}).get('id') for s in ('home','away')))
            row['pregame']={'provider_timestamp':pre.get('metaData',{}).get('timeStamp'),'received_at':pm['received_at'],'response_sha256':pm['sha256'],'probable_pitchers':{s:pp.get(s,{}).get('id') for s in ('home','away')},'audit':audit}
        else: coverage['pregame_missing']+=1
        if post:
            coverage['postgame_boxscore_raw']+=1; sides={}
            for side in ('home','away'):
                team=post.get('teams',{}).get(side,{}); players=team.get('players',{}); initial=[]; starters=[]; pitcher_box=[]
                for v in players.values():
                    code=v.get('battingOrder'); player=v.get('person',{}).get('id'); pitch=v.get('stats',{}).get('pitching',{})
                    if code in [str(i*100) for i in range(1,10)]: initial.append((int(code),player))
                    if pitch.get('gamesStarted')==1: starters.append(player)
                    if player in team.get('pitchers',[]): pitcher_box.append({'player_id':player,'inningsPitched':pitch.get('inningsPitched'),'numberOfPitches':pitch.get('numberOfPitches'),'strikeOuts':pitch.get('strikeOuts'),'baseOnBalls':pitch.get('baseOnBalls'),'gamesStarted':pitch.get('gamesStarted')})
                sides[side]={'initial_batting_order_ids':[i for _,i in sorted(initial)],'final_battingOrder_ids':team.get('battingOrder',[]),'actual_starting_pitcher_ids':starters,'pitcher_box':pitcher_box,'runs':team.get('teamStats',{}).get('batting',{}).get('runs')}
            coverage['both_postgame_starter_identified']+=int(all(len(sides[s]['actual_starting_pitcher_ids'])==1 for s in ('home','away')))
            coverage['both_initial_codes_nine']+=int(all(len(sides[s]['initial_batting_order_ids'])==9 for s in ('home','away')))
            coverage['postgame_final_battingOrder_differs_initial']+=int(any(sides[s]['initial_batting_order_ids']!=sides[s]['final_battingOrder_ids'] for s in ('home','away')))
            row['postgame']={'received_at':bm['received_at'],'response_sha256':bm['sha256'],'sides':sides}
        else: coverage['postgame_boxscore_missing']+=1
        if pre and post:
            coverage['both_raw']+=1
            coverage['both_probables_match_actual']+=int(all(row['pregame']['probable_pitchers'][s] in row['postgame']['sides'][s]['actual_starting_pitcher_ids'] for s in ('home','away')))
            coverage['pregame_lineups_match_postgame_initial']+=int(all(row['pregame']['audit']['lineups'][s]['starting_codes_100_to_900']==row['postgame']['sides'][s]['initial_batting_order_ids'] for s in ('home','away')))
        results.append(row)
    body=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in results).encode('utf8'); (P/'fixtures.jsonl').write_bytes(body)
    evidence={}
    for file in sorted(E.glob('*.meta.json')):
        name=file.name[:-10]; _,meta=read(name); meta['stored_gzip_sha256']=hashlib.sha256((E/(name+'.json.gz')).read_bytes()).hexdigest(); evidence[name]=meta
    price_bytes=(P/'price-join.json').read_bytes(); prices=json.loads(price_bytes); price_ids={x['gamePk'] for x in prices['records']}
    coverage['retrospective_price_and_both_raw']=sum(1 for x in results if x['gamePk'] in price_ids and x['pregame'] and x['postgame'])
    coverage['retrospective_price_and_replay_pass']=sum(1 for x in results if x['gamePk'] in price_ids and x['pregame'] and x['postgame'] and x['pregame']['audit']['event_replay_checks_pass'])
    summary={'status':'complete_raw_capture_pit_unverified' if coverage['both_raw']==102 else 'partial_raw_capture_pit_unverified','schedule_rows':len(rows),'census_unique_gamePk':len(games),'duplicate_gamePk':sum(n>1 for n in counts.values()),'unselected_gamePk':len(games)-len(chosen),'coverage':dict(coverage),'strict_pit_certified':0,'observed_price_verified':0,'exact_market_linked':len(price_ids),'price_join_sha256':hashlib.sha256(price_bytes).hexdigest(),'raw_request_count_logged':len(evidence),'request_limit':500,'schedule_sha256':sm['sha256'],'fixtures_sha256':hashlib.sha256(body).hexdigest(),'evidence':evidence}
    (P/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='evidence'},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
