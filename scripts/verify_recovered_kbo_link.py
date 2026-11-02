"""Verify a recovered pregame record/price/probable-pitcher/result association.

Does not backfill the operational ledger or claim confirmed lineups/model uplift.
"""
import argparse
import base64
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prediction_ledger import _sha256
from official_fact_capture import normalize_kbo_schedule, KBO_URL
from kbo_candidate_source import day_manifest


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone missing')
    return result


def raw_bytes(receipt):
    raw = base64.b64decode(receipt['raw_base64'], validate=True)
    if receipt.get('status') != 200 or receipt.get('url') != KBO_URL:
        raise ValueError('unexpected official source')
    if len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['raw_sha256']:
        raise ValueError('source bytes mismatch')
    return raw


def verify(snapshot, fact_capture, result_capture):
    rows = sorted(snapshot['prediction_tail'], key=lambda row: row['ledger_sequence'])
    if not rows:
        raise ValueError('empty ledger tail')
    for i, outer in enumerate(rows):
        record = outer['record']
        unsigned = {k:v for k,v in record.items() if k != 'record_hash'}
        if (_sha256(unsigned) != outer['record_hash'] or record['record_hash'] != outer['record_hash']
                or record['ledger_sequence'] != outer['ledger_sequence']):
            raise ValueError('record hash or sequence mismatch')
        if i and (outer['ledger_sequence'] != rows[i-1]['ledger_sequence']+1
                  or record['previous_record_hash'] != rows[i-1]['record_hash']):
            raise ValueError('broken tail chain')

    source_requests = [r for r in fact_capture['requests'] if r.get('url') == KBO_URL]
    if len(source_requests) != 1:
        raise ValueError('ambiguous pregame calendar source')
    receipt = source_requests[0]
    raw = raw_bytes(receipt)
    source = json.loads(raw)
    day = fact_capture['games'][0]['official_game_id'][:8]
    normalized = normalize_kbo_schedule(raw, expected_date=day, received_at=receipt['received_at'])
    if len(normalized['games']) != 1:
        raise ValueError('expected one verified official fixture')
    game = normalized['games'][0]
    official_id = game['official_game_id']
    official = next(r for r in source['game'] if r['G_ID'] == official_id)
    kickoff = instant(game['kickoff_at'])
    cutoff = kickoff - timedelta(minutes=30)
    candidates = []
    for outer in rows:
        r = outer['record']; fixture = r.get('features', {}).get('fixture', {})
        if (r['record_type'] == 'prediction' and fixture.get('league') == 'KBO'
            and fixture.get('home') == official['HOME_NM'] and fixture.get('away') == official['AWAY_NM']
            and instant(r['kickoff']) == kickoff):
            candidates.append(r)
    if len(candidates) != 1:
        raise ValueError('missing or ambiguous operating prediction')
    prediction = candidates[0]
    decision = instant(prediction['as_of'])
    if not instant(receipt['received_at']) <= decision <= instant(prediction['captured_at']) < cutoff:
        raise ValueError('source or prediction was too late')
    player_inputs = prediction['features'].get('research_player_inputs') or {}
    if player_inputs.get('source_game_id') != official_id:
        raise ValueError('saved player fixture differs')
    if instant(player_inputs['observed_at']) > decision or instant(player_inputs['source_updated_at']) > decision:
        raise ValueError('saved player input was too late')
    pitchers = [f for f in normalized['facts'] if f['kind'] == 'probable_starting_pitcher']
    if len(pitchers) != 2 or {f['after_value']['side'] for f in pitchers} != {'home','away'}:
        raise ValueError('missing probable pitcher sides')
    for fact in pitchers:
        v = fact['after_value']
        if player_inputs.get('starters', {}).get(v['side'], {}).get('name') != v['player_name']:
            raise ValueError('saved probable pitcher differs')
    fixture = prediction['features']['fixture']; pick = prediction['predictions']
    if pick.get('market') != '승패' or pick.get('label') or pick.get('selection') not in ('홈','원정'):
        raise ValueError('unsupported selected product')
    frozen_options = [p for p in prediction['features']['options']
        if p.get('market') == '승패' and not p.get('label')]
    if len(frozen_options) != 2 or {p.get('selection') for p in frozen_options} != {'홈','원정'}:
        raise ValueError('incomplete frozen price vector')
    frozen_book = {p['selection']: Decimal(str(p['odds'])) for p in frozen_options}
    quoted = [p for p in frozen_options if p['selection'] == pick['selection']]
    if (len(quoted) != 1 or quoted[0]['selection_id'] != pick['selection_id']
            or quoted[0]['offer_id'] != pick['offer_id']
            or frozen_book[pick['selection']] != Decimal(str(pick['odds']))):
        raise ValueError('frozen selection price differs')
    matching_prices = [p for p in snapshot['fixture_prices'] if
        p['season'] == fixture['year'] and p['round'] == fixture['round']
        and p['market_family'] == pick['market'] and p['n_way'] == 2
        and (p.get('market_label') or '') == ''
        and p['home'] == fixture['home'] and p['away'] == fixture['away']
        and p['date_text'] == fixture['date']]
    market_at = instant(prediction['market_observed_at'])
    exact = [p for p in matching_prices if instant(p['observed_at']) == market_at]
    if exact:
        if len(exact) != 1:
            raise ValueError('ambiguous exact saved price')
        price, price_link_kind = exact[0], 'same_observation_timestamp'
    else:
        # Independent collectors use different clocks: the ledger freezes a full
        # price vector, while snapshots record their own observation. Preserve
        # that distinction instead of inventing an exact original receipt.
        corroborating = [p for p in matching_prices if market_at <= instant(p['observed_at']) <= decision]
        identities = {(p['season'],p['round'],p['game_no']) for p in corroborating}
        if not corroborating or len(identities) != 1:
            raise ValueError('missing or ambiguous predecision price corroboration')
        latest = max(instant(p['observed_at']) for p in corroborating)
        latest_rows = [p for p in corroborating if instant(p['observed_at']) == latest]
        if len(latest_rows) != 1:
            raise ValueError('ambiguous latest corroborating price')
        price, price_link_kind = latest_rows[0], 'frozen_vector_with_predecision_corroboration'
    if not market_at <= instant(price['observed_at']) <= decision:
        raise ValueError('price was too late')
    selected = 0 if pick['selection'] == '홈' else 1
    if len(price['odds']) != 2 or [Decimal(str(v)) for v in price['odds']] != [frozen_book['홈'],frozen_book['원정']]:
        raise ValueError('frozen price differs')
    label_receipt = result_capture['request']
    label_raw = raw_bytes(label_receipt)
    parsed = day_manifest(json.loads(label_raw), day=kickoff.date().isoformat(),
        observed_at=label_receipt['received_at'], source_sha256=label_receipt['raw_sha256'])
    final = [r for r in parsed['results'] if r['source_fixture_id'] == 'kbo:'+official_id
             and r['home'] == fixture['home'] and r['away'] == fixture['away']]
    if len(final) != 1 or instant(label_receipt['received_at']) <= kickoff:
        raise ValueError('missing or invalid official final')
    result = final[0]
    if result['hg'] == result['ag']:
        grade, profit = 'requires_draw_settlement_rules', None
    else:
        hit = (result['hg'] > result['ag']) == (selected == 0)
        grade = 'hit' if hit else 'miss'
        profit = str(Decimal(str(pick['odds']))-1 if hit else Decimal(-1))
    return {'schema_version':'recovered-kbo-link-verification-v1',
        'ledger_tail':{'rows':len(rows),'first_sequence':rows[0]['ledger_sequence'],
            'last_sequence':rows[-1]['ledger_sequence'],'individual_hashes_verified':True,
            'adjacent_hash_chain_verified':True,'full_prefix_verified':False},
        'recovered_operating_links':1,'official_game_id':official_id,
        'event_id':prediction['event_id'],'snapshot_id':prediction['snapshot_id'],
        'kickoff':prediction['kickoff'],'decision_at':prediction['as_of'],
        'prediction_captured_at':prediction['captured_at'],
        'official_fact_received_at':receipt['received_at'],
        'price_observed_at':price['observed_at'],'price_game_no':price['game_no'],
        'ledger_market_observed_at':prediction['market_observed_at'],
        'price_link_kind':price_link_kind,
        'exact_external_original_price_snapshot':bool(exact),
        'selection':pick['selection'],'odds':pick['odds'],
        'pitcher_player_ids':[f['after_value']['player_id'] for f in pitchers],
        'pitcher_status':'probable_not_final_confirmed','confirmed_lineup':False,
        'confirmed_player_model_eligible':False,'result_score':[result['hg'],result['ag']],
        'result_received_at':label_receipt['received_at'],'grade':grade,
        'descriptive_single_unit_profit':profit,'model_uplift_evaluated':False,
        'independent_receipt_authentication':False,
        'limitations':['One recovered event is not an accuracy or ROI improvement experiment.',
            'Hash checks do not independently authenticate the source receipt time.',
            'Stored price observation does not prove executable purchase or raw price response receipt.',
            'No operational records, predictions, or wallet transactions are changed.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot','facts','result','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    inputs = [args.snapshot, args.facts, args.result]
    report = verify(*(json.loads(p.read_text(encoding='utf8')) for p in inputs))
    report['source_files'] = [{'path':p.as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]
    with args.output.open('x', encoding='utf8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:report[k] for k in ('recovered_operating_links','official_game_id','grade','model_uplift_evaluated')},ensure_ascii=True))


if __name__ == '__main__':
    main()
