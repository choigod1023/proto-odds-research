"""Offline research-only verified-fact input boundary. No model is fitted here.

All registries, source timestamps and hashes are caller assertions, not proof of
independent receipt or source authenticity. No network, clock or storage access.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
import re
from urllib.parse import urlsplit


class InvalidFact(ValueError):
    """An input cannot safely be used for pregame research."""


def _shape(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise InvalidFact('missing or unexpected fields')


def _str(value):
    if not isinstance(value, str) or not value.strip():
        raise InvalidFact('nonempty identifier required')
    return value


def _time(value):
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.tzinfo is None or d.utcoffset() is None: raise ValueError
        return d
    except (AttributeError, TypeError, ValueError):
        raise InvalidFact('timezone-aware timestamp required') from None


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}',value) is None:
        raise InvalidFact('invalid SHA256')


def _url(value):
    if not isinstance(value,str): raise InvalidFact('source URL required')
    u=urlsplit(value)
    if u.scheme not in ('http','https') or not u.netloc or u.username or u.password:
        raise InvalidFact('invalid source URL')


def _number(value):
    if type(value) not in (float,int) or not math.isfinite(value) or value < 0:
        raise InvalidFact('invalid nonnegative number')
    return value


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _evidence(e, decision, official=False):
    keys=('url','published_at','received_at','raw_sha256') + (('identity_checked','confirmed') if official else ())
    _shape(e,keys); _url(e['url']);_sha(e['raw_sha256'])
    received=_time(e['received_at'])
    if received>decision: raise InvalidFact('source received after decision')
    if e['published_at'] is not None and _time(e['published_at'])>received:
        raise InvalidFact('publication claim after receipt')
    if _time(e['published_at']) > _time(e['received_at']): raise InvalidFact('published after receipt')
    if official and (e['identity_checked'] is not True or e['confirmed'] is not True):
        raise InvalidFact('official identity or confirmation missing')


def bullpen_workload(records, expected_game_ids, decision_at, target_game_id, team_id, player_ids, hours):
    """Sum prior completed official boxscore pitch counts; completeness is caller-attested.

    expected_game_ids must come from a verified complete schedule for this window;
    None is unknown and rejected. Count target-game pitches and future info never.
    """
    if hours not in (24,48) or not isinstance(expected_game_ids,list) or not isinstance(records,list):
        raise InvalidFact('unknown workload coverage or invalid window')
    if any(not isinstance(x,str) or not x for x in expected_game_ids) or len(set(expected_game_ids))!=len(expected_game_ids):
        raise InvalidFact('invalid expected games')
    decision=_time(decision_at); seen=set(); total=0
    for rec in records:
        _shape(rec,('official_game_id','team_id','status','ended_at','received_at','official_url','raw_sha256','identity_checked','pitch_counts'))
        gid=_str(rec['official_game_id'])
        if gid==target_game_id or gid in seen or gid not in expected_game_ids or rec['team_id']!=team_id:
            raise InvalidFact('wrong, duplicate or target boxscore')
        seen.add(gid); ended=_time(rec['ended_at']);received=_time(rec['received_at'])
        if rec['status']!='final' or rec['identity_checked'] is not True or not decision-timedelta(hours=hours)<=ended<=received<=decision:
            raise InvalidFact('boxscore not final or unavailable in window')
        _url(rec['official_url']);_sha(rec['raw_sha256'])
        counts=rec['pitch_counts']
        if not isinstance(counts,dict) or any(not isinstance(k,str) or not k or type(v) is not int or v<0 for k,v in counts.items()):
            raise InvalidFact('invalid pitch counts')
        if any(player not in counts for player in player_ids): raise InvalidFact('player workload missing in boxscore')
        total+=sum(counts[player] for player in player_ids)
    if seen!=set(expected_game_ids): raise InvalidFact('incomplete prior boxscores')
    return total


@dataclass(frozen=True)
class FactFeature:
    fact_id: str
    kind: str
    league: str
    sport: str
    player_ids: tuple
    before_value: object
    after_value: object
    units: str
    before_available: bool
    reason: str
    revision: int
    claim_raw_sha256: str
    official_raw_sha256: str
    externally_verified: bool = False


def extract_feature(fact, registry, *, workload=None):
    """Validate one exact fact against a caller-provided identity/product registry."""
    _shape(fact,('fact_id','canonical_game_id','official_game_id','league','sport','kind','before_value','after_value','units','player_ids','claim','official','decision_at','kickoff_at','market','revision'))
    _shape(registry,('canonical_game_id','official_game_id','league','sport','kickoff_at','home_team_id','away_team_id','player_teams','product'))
    for key in ('fact_id','canonical_game_id','official_game_id','league','sport','kind','units'):_str(fact[key])
    for key in ('canonical_game_id','official_game_id','league','sport'):
        if fact[key]!=registry[key]: raise InvalidFact('game identity mismatch')
    if _time(fact['kickoff_at'])!=_time(registry['kickoff_at']): raise InvalidFact('kickoff mismatch')
    decision=_time(fact['decision_at']); kickoff=_time(fact['kickoff_at'])
    if decision>kickoff-timedelta(minutes=30): raise InvalidFact('decision after T-30')
    if type(fact['revision']) is not int or fact['revision']<1: raise InvalidFact('invalid revision')
    _evidence(fact['claim'],decision);_evidence(fact['official'],decision,True)
    home=_str(registry['home_team_id']);away=_str(registry['away_team_id'])
    if home==away: raise InvalidFact('same teams')
    ids=fact['player_ids']; mappings=registry['player_teams']
    if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or not x for x in ids) or len(ids)!=len(set(ids)) or not isinstance(mappings,dict):
        raise InvalidFact('invalid player IDs')
    if any(pid not in mappings or mappings[pid] not in (home,away) for pid in ids): raise InvalidFact('unknown player identity')
    market=fact['market']; _shape(market,('product_id','market','period','line','outcomes','odds','observed_at','received_at'))
    product=registry['product'];_shape(product,('product_id','market','period','line','outcomes'))
    if {k:market[k] for k in product}!=product: raise InvalidFact('product mismatch')
    _str(product['product_id'])
    if product['market']!='win' or product['period']!='full' or product['line'] is not None or product['outcomes'] not in (['home','away'],['home','draw','away']):
        raise InvalidFact('unsupported market')
    odds=market['odds']
    if not isinstance(odds,dict) or set(odds)!=set(product['outcomes']) or any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in odds.values()):
        raise InvalidFact('incomplete or invalid odds')
    if not _time(market['observed_at'])<=_time(market['received_at'])<=decision: raise InvalidFact('market unavailable')
    kind=fact['kind']; before=fact['before_value']; after=fact['after_value']
    if kind in ('confirmed_starter_change','official_absence'):
        _shape(after,('player_id','status'))
        wanted='confirmed' if kind=='confirmed_starter_change' else 'absent'
        if after['status']!=wanted or after['player_id'] not in ids: raise InvalidFact('after status is not confirmed for player')
        if kind=='confirmed_starter_change' and fact['sport']!='baseball': raise InvalidFact('starter requires baseball')
        if fact['units']!='player': raise InvalidFact('invalid player units')
        if before is not None:
            _shape(before,('player_id','status'))
            if before['player_id'] not in ids or before['status'] not in ('probable','confirmed','available','absent'):
                raise InvalidFact('invalid prior player status')
            if mappings[before['player_id']] != mappings[after['player_id']]: raise InvalidFact('different team in player change')
    elif kind=='confirmed_lineup_change':
        if fact['units']!='players' or not isinstance(after,list) or not after: raise InvalidFact('confirmed lineup required')
        for player in after:
            _shape(player,('player_id','status'))
            if player['status']!='confirmed' or player['player_id'] not in ids: raise InvalidFact('unconfirmed lineup')
        if len({p['player_id'] for p in after})!=len(after) or len({mappings[p['player_id']] for p in after})!=1: raise InvalidFact('invalid lineup team or duplicate')
        if before is not None:
            if not isinstance(before,list): raise InvalidFact('invalid earlier lineup')
            for player in before:
                _shape(player,('player_id','status'))
                if player['player_id'] not in ids or player['status'] not in ('confirmed','probable'):
                    raise InvalidFact('invalid earlier lineup')
    elif kind in ('bullpen_workload_24h','bullpen_workload_48h'):
        if fact['sport']!='baseball' or fact['units']!='pitches' or workload is None: raise InvalidFact('workload evidence unavailable')
        _shape(workload,('records','expected_game_ids','team_id','schedule_complete'))
        if workload['schedule_complete'] is not True or any(mappings[pid]!=workload['team_id'] for pid in ids): raise InvalidFact('unverified schedule or team')
        if before is not None and (type(before) is not int or before < 0): raise InvalidFact('invalid prior workload')
        if workload['schedule_complete'] is not True or any(mappings[p]!=workload['team_id'] for p in ids): raise InvalidFact('unverified workload coverage or team')
        hours=24 if kind.endswith('24h') else 48
        calculated=bullpen_workload(workload['records'],workload['expected_game_ids'],fact['decision_at'],fact['official_game_id'],workload['team_id'],ids,hours)
        if _number(after)!=calculated: raise InvalidFact('workload disagrees with boxscores')
        if before is not None: _number(before)
    else: raise InvalidFact('unsupported fact kind')
    return FactFeature(fact['fact_id'],kind,fact['league'],fact['sport'],tuple(ids),before,after,fact['units'],before is not None,'available' if before is not None else 'earlier_value_unavailable',fact['revision'],fact['claim']['raw_sha256'],fact['official']['raw_sha256'])


def build_research_envelope(facts, registry, baseline, market, *, workloads=None):
    """Emit features and unchanged baseline; learned player inference is unavailable."""
    if not isinstance(facts,list): raise InvalidFact('facts must be a list')
    _shape(registry,('canonical_game_id','official_game_id','league','sport','kickoff_at','home_team_id','away_team_id','player_teams','product'))
    _shape(baseline,('canonical_game_id','product_id','decision_at','outcomes','probability_vector','input_content_sha256','freeze_hash'))
    if baseline['canonical_game_id']!=registry['canonical_game_id'] or baseline['product_id']!=registry['product']['product_id'] or baseline['outcomes']!=registry['product']['outcomes']:
        raise InvalidFact('baseline identity mismatch')
    decision=_time(baseline['decision_at']);kickoff=_time(registry['kickoff_at'])
    if decision>kickoff-timedelta(minutes=30): raise InvalidFact('baseline after T-30')
    _shape(market,('product_id','market','period','line','outcomes','odds','observed_at','received_at'))
    product=registry['product']; _shape(product,('product_id','market','period','line','outcomes'))
    if {k:market[k] for k in product}!=product or product['market']!='win' or product['period']!='full' or product['line'] is not None or product['outcomes'] not in (['home','away'],['home','draw','away']): raise InvalidFact('unsupported market')
    odds=market['odds']
    if not isinstance(odds,dict) or set(odds)!=set(product['outcomes']) or any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in odds.values()): raise InvalidFact('incomplete or invalid odds')
    if not _time(market['observed_at'])<=_time(market['received_at'])<=decision: raise InvalidFact('market unavailable')
    _sha(baseline['input_content_sha256']);_sha(baseline['freeze_hash'])
    probabilities=baseline['probability_vector']
    if not isinstance(probabilities,list) or len(probabilities)!=len(baseline['outcomes']) or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=1 for x in probabilities) or not math.isclose(sum(probabilities),1,abs_tol=1e-9,rel_tol=0):
        raise InvalidFact('invalid baseline probabilities')
    if workloads is None: workloads={}
    if not isinstance(workloads,dict): raise InvalidFact('invalid workload map')
    features=[];seen=set()
    for fact in facts:
        if not isinstance(fact,dict) or fact.get('fact_id') in seen: raise InvalidFact('duplicate or invalid fact')
        seen.add(fact.get('fact_id'))
        if _time(fact.get('decision_at'))!=decision: raise InvalidFact('fact decision mismatch')
        if fact.get('market') != market: raise InvalidFact('fact market mismatch')
        features.append(asdict(extract_feature(fact,registry,workload=workloads.get(fact.get('fact_id')))))
    result={'schema_version':'fact-delta-research-v1','canonical_game_id':baseline['canonical_game_id'],'product_id':baseline['product_id'],'decision_at':baseline['decision_at'],
            'probability_vector':list(probabilities),'outcomes':list(baseline['outcomes']),'baseline_input_content_sha256':baseline['input_content_sha256'],
            'baseline_freeze_hash':baseline['freeze_hash'],'features':features,'availability_reason':'candidate_model_unavailable',
            'external_receipt_verified':False,'prediction_changed':False}
    # Detach all nested values: callers cannot mutate an already returned snapshot.
    result=json.loads(_canonical(result))
    result['research_envelope_sha256']=hashlib.sha256(_canonical(result).encode('utf8')).hexdigest()
    return result
