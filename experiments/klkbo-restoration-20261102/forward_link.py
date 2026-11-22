"""Join one preserved official probable-pitcher response to a read-only DB export."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


def dt(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None: raise ValueError('naive timestamp')
    return value.astimezone(timezone.utc)


def ledger_tail_integrity(envelopes):
    records = sorted((e['record'] for e in envelopes), key=lambda x:x.get('ledger_sequence',0))
    if not records or any('record_hash' not in r for r in records):
        return {'status':'not_verifiable', 'rows':len(records)}
    matches = 0
    for r in records:
        unsigned = {k:v for k,v in r.items() if k != 'record_hash'}
        canonical = json.dumps(unsigned,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        matches += hashlib.sha256(canonical.encode('utf8')).hexdigest() == r['record_hash']
    adjacent = sum(b['ledger_sequence'] == a['ledger_sequence']+1 and
                   b.get('previous_record_hash') == a['record_hash'] for a,b in zip(records,records[1:]))
    return {'rows':len(records), 'record_hash_matches':matches, 'adjacent_chain_links':adjacent,
            'first_sequence':records[0]['ledger_sequence'], 'last_sequence':records[-1]['ledger_sequence'],
            'prefix_verified':False}


def link(snapshot, capture):
    requests = [r for r in capture['requests'] if r.get('status') == 200 and r.get('raw_base64')
                and r.get('url','').endswith('/GetKboGameList')]
    if len(requests) != 1: raise ValueError('official response ambiguous')
    request = requests[0]
    raw = base64.b64decode(request['raw_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != request['raw_sha256']: raise ValueError('official bytes hash mismatch')
    games = json.loads(raw)['game']
    relevant = [g for g in games if str(g.get('G_DT')) == '20261101' and
                g.get('HOME_ID') == 'SS' and g.get('AWAY_ID') == 'HT' and g.get('G_TM') == '14:00']
    if len(relevant) != 1: raise ValueError('official fixture missing or ambiguous')
    official = relevant[0]
    if str(official.get('SEASON_ID')) != '2026': raise ValueError('season mismatch')
    kickoff = datetime.fromisoformat('2026-11-01T14:00:00+09:00').astimezone(timezone.utc)
    cutoff = kickoff - timedelta(minutes=30)
    if dt(request['received_at']) > cutoff: raise ValueError('official response late')
    facts = [x for x in capture['facts'] if x.get('official_game_id') == official['G_ID']]
    expected = {'home':('SS',str(official['B_PIT_P_ID'])), 'away':('HT',str(official['T_PIT_P_ID']))}
    actual = {(x['after_value']['side'], x['after_value']['team_id'], str(x['after_value']['player_id'])) for x in facts}
    if actual != {(s,t,p) for s,(t,p) in expected.items()} or len(facts) != 2:
        raise ValueError('probable player identity mismatch')
    expected_names = {'home':str(official['B_PIT_P_NM']).strip(), 'away':str(official['T_PIT_P_NM']).strip()}
    if any(x['after_value'].get('player_name') != expected_names[x['after_value']['side']]
           or x['official'].get('raw_sha256') != request['raw_sha256']
           or x['official'].get('received_at') != request['received_at'] for x in facts):
        raise ValueError('probable fact disagrees with preserved response')
    if any(x['after_value'].get('announcement_status') != 'probable' or x['official'].get('confirmed') for x in facts):
        raise ValueError('not an unconfirmed probable announcement')
    candidates = []
    for envelope in snapshot['prediction_tail']:
        r = envelope['record']; fixture = r.get('features',{}).get('fixture',{})
        if (envelope.get('record_type') == 'prediction' and fixture.get('year') == 2026
            and fixture.get('league') == 'KBO' and fixture.get('sport') == 'bs'
            and fixture.get('home') == '삼성' and fixture.get('away') == 'KIA'
            and fixture.get('date','').startswith('11.01(')
            and fixture.get('date','').endswith('14:00') and dt(r['kickoff']) == kickoff
            and dt(r['captured_at']) <= cutoff):
            candidates.append(r)
    event_ids = {r['event_id'] for r in candidates}
    if len(event_ids) != 1: raise ValueError('operating event ambiguous or missing')
    candidates.sort(key=lambda r: (dt(r['captured_at']), r['ledger_sequence']))
    r = candidates[-1]
    decision_time = min(dt(r['captured_at']), dt(r['as_of']))
    if dt(request['received_at']) > decision_time:
        raise ValueError('probable announcement received after revision')
    player_input = r.get('features',{}).get('research_player_inputs') or {}
    starters = player_input.get('starters') or {}
    names = expected_names
    player_crosscheck = (player_input.get('source_game_id') == official['G_ID'] and
                         all((starters.get(side) or {}).get('name') == name for side,name in names.items()) and
                         bool(player_input.get('observed_at')) and
                         dt(player_input['observed_at']) <= decision_time)
    opts = [x for x in r.get('features',{}).get('options',[]) if x.get('market') == '승패'
            and not x.get('label') and x.get('selection') in ('홈','원정')]
    if len(opts) != 2 or {x['selection'] for x in opts} != {'홈','원정'}:
        raise ValueError('incomplete or ambiguous decision options')
    odds = {x['selection']:x['odds'] for x in opts}
    quotes = [p for p in snapshot['fixture_prices'] if p.get('season') == 2026 and p.get('league') == 'KBO'
              and p.get('sport') == 'bs' and p.get('home') == '삼성' and p.get('away') == 'KIA'
              and p.get('date_text','').startswith('11.01(') and p.get('date_text','').endswith('14:00')
              and p.get('market_family') == '승패' and p.get('n_way') == 2 and not p.get('market_label')
              and dt(p['observed_at']) <= cutoff]
    exact_time = [p for p in quotes if dt(p['observed_at']) == dt(r['market_observed_at'])]
    matching = [p for p in quotes if p.get('odds') == [odds['홈'], odds['원정']]]
    matching_before = [p for p in matching if dt(p['observed_at']) <= decision_time]
    documents = [x for x in snapshot['documents'] if x['name'] == 'kbo_candidate_calendar_cache']
    if len(documents) != 1: raise ValueError('calendar missing or ambiguous')
    calendar = documents[0]['payload']['calendar_cache'].get('2026-11-01', {})
    results = [x for x in calendar.get('results',[]) if x.get('source_fixture_id') == 'kbo:'+official['G_ID']
               and x.get('home') == '삼성' and x.get('away') == 'KIA']
    settled = [x['record'] for x in snapshot['prediction_tail'] if x.get('record_type') == 'settlement'
               and x['record'].get('snapshot_id') == r['snapshot_id']]
    return {'official_game_id':official['G_ID'], 'canonical_game_id':None,
            'identity_basis':'official game date, time and SS/HT team IDs cross-checked against Korean home/away names; no invented canonical ID',
            'kickoff_at':kickoff.isoformat(), 't30':cutoff.isoformat(),
            'official_probable':{'received_at':request['received_at'], 'sha256':request['raw_sha256'],
                'external_receipt_verified':False, 'final_starting_lineup_confirmed':False,
                'players':[x['after_value'] for x in facts]},
            'research_player_inputs':{'matches_official_id_and_names_before_revision':player_crosscheck,
                'source_game_id':player_input.get('source_game_id'), 'observed_at':player_input.get('observed_at'),
                'starters':starters, 'lineup_status':player_input.get('lineup_status'),
                'training_eligible':player_input.get('training_eligible')},
            'research_daily_state':r.get('features',{}).get('research_kbo_daily_state'),
            'ledger_tail_integrity':ledger_tail_integrity(snapshot['prediction_tail']),
            'operating_event_id':r['event_id'], 'available_pregame_revision_count':len(candidates),
            'latest_revision_in_partial_tail':{'snapshot_id':r['snapshot_id'], 'captured_at':r['captured_at'],
                'market_observed_at':r['market_observed_at'], 'odds':odds, 'prediction':r['predictions'],
                'tipster_hybrid':r['features'].get('tipster_hybrid'), 'frozen_status_verified':False},
            'price_rows_in_scope':len(quotes), 'matching_price_rows':len(matching),
            'matching_price_before_revision_count':len(matching_before),
            'matching_price_before_revision_latest':max((p['observed_at'] for p in matching_before),default=None),
            'exact_market_observation_time_rows':len(exact_time),
            'matching_price_game_numbers':sorted({p['game_no'] for p in matching}),
            'price_product_period_verified':False,
            'calendar_result_claim':results[0] if len(results)==1 else None,
            'calendar_cache_source_sha256':calendar.get('manifest',{}).get('source_sha256'),
            'calendar_raw_response_bytes_available':False,
            'exact_revision_settlement_count_in_partial_tail':len(settled),
            'evidence_layer':'prospective_local_probable_and_revision_link; full frozen/receipt/settlement verification incomplete',
            'limitations':['probable_pitchers_not_confirmed_lineup','external_receipt_not_independently_verified',
                'tail_is_not_full_ledger','frozen_revision_not_proven','price_product_period_not_proven',
                'official_result_raw_bytes_unavailable','settlement_not_found_in_partial_tail']}


def attach_official_result(row, result):
    request = result['request']
    if request.get('status') != 200 or not request.get('url','').endswith('/GetKboGameList'):
        raise ValueError('unverified result source')
    raw = base64.b64decode(request['raw_base64'],validate=True)
    if len(raw) != request['bytes'] or hashlib.sha256(raw).hexdigest() != request['raw_sha256']:
        raise ValueError('result bytes mismatch')
    if request['raw_sha256'] != row.get('calendar_cache_source_sha256'):
        raise ValueError('result raw does not match calendar source hash')
    matches = [g for g in json.loads(raw)['game'] if g.get('G_ID') == row['official_game_id']
               and str(g.get('SEASON_ID')) == '2026' and g.get('G_DT') == '20261101'
               and g.get('G_TM') == '14:00' and g.get('HOME_ID') == 'SS' and g.get('AWAY_ID') == 'HT'
               and str(g.get('GAME_STATE_SC')) == '3' and g.get('GAME_RESULT_CK') == 1]
    if len(matches) != 1: raise ValueError('final result identity or state mismatch')
    game = matches[0]
    score = [int(game['B_SCORE_CN']),int(game['T_SCORE_CN'])]
    cached = row.get('calendar_result_claim')
    if not cached or [cached['hg'],cached['ag']] != score:
        raise ValueError('result disagrees with preserved cache')
    if dt(request['received_at']) <= dt(row['kickoff_at']): raise ValueError('result received before kickoff')
    row['official_result_raw'] = {'home_score':score[0], 'away_score':score[1],
                                  'raw_sha256':request['raw_sha256'], 'received_at':request['received_at'],
                                  'use':'result_only', 'matches_cached_score':True}
    row['calendar_raw_response_bytes_available'] = True
    row['limitations'].remove('official_result_raw_bytes_unavailable')
    return row


def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--capture',type=Path,required=True);p.add_argument('--result',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    snapshot_bytes=a.snapshot.read_bytes();capture_bytes=a.capture.read_bytes()
    row=link(json.loads(snapshot_bytes),json.loads(capture_bytes))
    row['source_hashes']={'snapshot_sha256':hashlib.sha256(snapshot_bytes).hexdigest(),
                          'capture_sha256':hashlib.sha256(capture_bytes).hexdigest()}
    row['source_paths']={'snapshot':str(a.snapshot),'capture':str(a.capture)}
    if a.result:
        result_bytes=a.result.read_bytes()
        attach_official_result(row,json.loads(result_bytes))
        row['source_hashes']['result_sha256']=hashlib.sha256(result_bytes).hexdigest()
        row['source_paths']['result']=str(a.result)
    a.out.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in row.items() if k not in ('latest_revision_in_partial_tail','official_probable')},ensure_ascii=True,indent=2))


if __name__ == '__main__': main()
