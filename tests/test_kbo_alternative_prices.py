import copy
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from research.kbo_forward.prices import DETAIL_URL
from research.m1_service.alternative_prices import parse_detail
from research.m1_service.low_odds import review

BODY = Path('experiments/kbo-alternative-prices-20261104/detail-2026-48.body.gz')


def sample():
    data = json.loads(gzip.decompress(BODY.read_bytes()))
    s = data['compSchedules']; keys = s['keys']
    rows = [dict(zip(keys, x)) for x in s['datas']]
    row = next(x for x in rows if x.get('leagueCode') == 'BS001' and x.get('betId') == '79' and x.get('matchSeq') == 760)
    start = datetime.fromtimestamp(row['gameDate']/1000, timezone.utc)
    # Only the test sale flag and test timestamps are synthetic; source identity,
    # labels, line and prices remain the preserved official response values.
    row['protoStatus'] = '2'
    data['compSchedules']['datas'] = [[row[k] for k in keys]]
    raw = json.dumps(data, ensure_ascii=False).encode()
    receipt = (start.timestamp()-3600)
    t = lambda n: datetime.fromtimestamp(n, timezone.utc).isoformat()
    round_info = {'gmId':'G101','gmTs':'260048','saleStartDate':(receipt-3600)*1000,'saleEndDate':(receipt+7200)*1000}
    fixtures = [{'official_game_id':'fixture-1','home_id':row['homeId'],'away_id':row['awayId'],'kickoff_at':start.isoformat(),'season':2026}]
    evidence = {'uri': DETAIL_URL, 'sha256':hashlib.sha256(raw).hexdigest(), 'received_at':t(receipt)}
    return data, round_info, fixtures, t(receipt), evidence, t(receipt)


def run(args):
    data, rnd, games, receipt, evidence, asof = args
    raw = json.dumps(data, ensure_ascii=False).encode()
    evidence = dict(evidence, sha256=hashlib.sha256(raw).hexdigest())
    return parse_detail(raw, rnd, games, receipt, evidence, asof)


def test_actual_terms_connect_to_review():
    offers, excluded = run(sample())
    assert not excluded
    assert [(o['selection'],o['line'],o['odds']) for o in offers] == [('under',8.5,1.91),('over',8.5,1.6)]
    forecast = {'fixture_id':'fixture-1','home_rate':4.5,'away_rate':4.0,'probabilities_3way':{'home':.48,'away':.42,'draw':.1}}
    result = review(forecast, offers, offers[0]['offer_id'])
    assert len(result['candidates']) == 2 and not result['excluded']
    assert result['automatic_replacement'] is None


def test_fail_closed_conditions():
    original = sample()
    for field, value, reason in [('loseHandi',9.5,'unsupported_or_mismatched_line'),('winHandi',8,'unsupported_or_mismatched_line'),('winTxt','오버','product_terms_mismatch'),('protoStatus','4','not_confirmed_on_sale'),('live','Y','not_confirmed_on_sale')]:
        args = copy.deepcopy(original); s=args[0]['compSchedules']; s['datas'][0][s['keys'].index(field)] = value
        q, x = run(args); assert not q and x[0]['reason'] == reason
    args=copy.deepcopy(original); args[2].append(copy.deepcopy(args[2][0])); assert run(args)[1][0]['reason']=='fixture_unmatched_or_ambiguous'
    args=copy.deepcopy(original); args[0]['compSchedules']['datas'].append(copy.deepcopy(args[0]['compSchedules']['datas'][0])); assert not run(args)[0]
    args=list(copy.deepcopy(original)); args[5]='2026-04-24T09:01:00+00:00'; assert not run(args)[0]


def test_receipt_hash_and_historical_evidence():
    args=sample(); data,rnd,games,receipt,evidence,asof=args
    raw=json.dumps(data,ensure_ascii=False).encode()
    with pytest.raises(ValueError,match='sha256'): parse_detail(raw+b' ',rnd,games,receipt,evidence,asof)
    with pytest.raises(ValueError,match='receipt_after_decision'): parse_detail(raw,rnd,games,receipt,evidence,'2020-01-01T00:00:00+00:00')
    raw=gzip.decompress(BODY.read_bytes()); evidence=dict(evidence,sha256=hashlib.sha256(raw).hexdigest(),received_at='2026-11-03T01:36:31.017533+00:00')
    offers,excluded=parse_detail(raw,rnd,games,evidence['received_at'],evidence,evidence['received_at'])
    assert not offers and excluded


def test_boundaries_and_handicap_hold():
    from datetime import timedelta
    args = list(sample())
    start = datetime.fromisoformat(args[2][0]['kickoff_at'])
    args[5] = (start - timedelta(minutes=30)).isoformat()
    assert len(run(args)[0]) == 2
    args[5] = (start - timedelta(minutes=30) + timedelta(microseconds=1)).isoformat()
    assert not run(args)[0]
    args = list(sample()); s = args[0]['compSchedules']; keys=s['keys']
    for k,v in {'betId':'7','betTypId':'5','betNm':'야구 핸디캡','betTypNm':'일반 소수핸디캡'}.items():
        s['datas'][0][keys.index(k)] = v
    assert run(args)[1][0]['reason'] == 'handicap_draw_settlement_unverified'


def test_duplicate_different_markets_and_round_validation():
    args=list(sample()); s=args[0]['compSchedules']; row=copy.deepcopy(s['datas'][0]); row[s['keys'].index('matchSeq')]=761; s['datas'].append(row)
    offers, excluded=run(args)
    assert not offers and len([x for x in excluded if x['reason']=='multiple_market_candidates'])==2
    args=list(sample()); args[1]['gmTs']='260049'
    with pytest.raises(ValueError,match='round_mismatch'): run(args)
