"""Offline-capable event store for prediction inputs; no collectors or model execution.

Callers must supply actual receipt timestamps from a trusted ingress boundary.
SQLite state and hashes alone cannot authenticate provider identity or timestamps.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

SCHEMA = 'prediction-observation-v1'
KINDS = {'odds', 'lineup', 'injury', 'substitution', 'settlement'}
PHASES = {'pregame', 'inplay'}


class ObservationError(ValueError):
    """Invalid observation or conflicting immutable revision."""


def _time(value: Any, field: str) -> datetime:
    try:
        if not isinstance(value, str): raise ValueError()
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.tzinfo is None or d.utcoffset() is None: raise ValueError()
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ObservationError(f'{field} must be an aware timestamp') from exc


def _stamp(value: Any, field: str) -> str:
    return _time(value, field).isoformat(timespec='microseconds')


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ObservationError('not finite JSON') from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ObservationError(f'{name} is required')
    return value


def _keys(obj: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(obj, dict) or not required <= obj.keys() or obj.keys() - required - optional:
        raise ObservationError('missing or unexpected fields')


def normalize(observation: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    """Validate, detach and normalize one provider revision and its raw bytes."""
    record = json.loads(_json(observation))
    fields = {'schema_version', 'provider', 'provider_event_id', 'provider_revision_id',
              'provider_game_id', 'canonical_game_id', 'stream_id', 'sequence', 'kind',
              'phase', 'kickoff_at', 'source_url', 'source_published_at_claim',
              'received_at', 'source_sha256', 'external_receipt_verified', 'payload'}
    _keys(record, fields, {'supersedes_revision_id'})
    if record['schema_version'] != SCHEMA: raise ObservationError('unknown schema')
    for name in ('provider', 'provider_event_id', 'provider_revision_id', 'provider_game_id',
                 'canonical_game_id', 'stream_id', 'source_url'):
        _required_text(record[name], name)
    if type(record['sequence']) is not int or record['sequence'] < 0: raise ObservationError('invalid sequence')
    if record['kind'] not in KINDS or record['phase'] not in PHASES: raise ObservationError('invalid kind or phase')
    if record['external_receipt_verified'] is not False: raise ObservationError('external witness not configured')
    if not isinstance(raw, bytes) or not raw: raise ObservationError('raw bytes required')
    if hashlib.sha256(raw).hexdigest() != record['source_sha256']: raise ObservationError('source hash mismatch')
    for key in ('kickoff_at', 'received_at'):
        record[key] = _stamp(record[key], key)
    claim = record['source_published_at_claim']
    if claim is not None:
        record['source_published_at_claim'] = _stamp(claim, 'source_published_at_claim')
        if record['source_published_at_claim'] > record['received_at']: raise ObservationError('claim after receipt')
    if record['phase'] == 'inplay' and record['received_at'] < record['kickoff_at']:
        raise ObservationError('inplay receipt before kickoff')
    if record.get('supersedes_revision_id') is not None:
        _required_text(record['supersedes_revision_id'], 'supersedes_revision_id')
    else:
        record['supersedes_revision_id'] = None
    p = record['payload']; kind = record['kind']
    if kind == 'odds':
        _keys(p, {'provider_product_id', 'market', 'period', 'line', 'settlement_scope', 'status', 'outcome_ids', 'outcomes', 'quote_at_claim', 'valid_until'})
        for key in ('provider_product_id','market','period','settlement_scope'): _required_text(p[key],key)
        if p['line'] is not None: _required_text(p['line'],'line')
        if p['status'] not in ('open','suspended','closed'): raise ObservationError('invalid market status')
        if not isinstance(p['outcomes'],dict): raise ObservationError('invalid outcomes')
        ids=p['outcome_ids']
        if not isinstance(ids,list) or len(ids)<2 or any(not isinstance(x,str) or not x.strip() for x in ids) or len(ids)!=len(set(ids)):
            raise ObservationError('invalid outcome identities')
        if not set(p['outcomes'])<=set(ids) or (p['status']=='open' and set(p['outcomes'])!=set(ids)):
            raise ObservationError('incomplete or mismatched odds')
        for key, price in p['outcomes'].items():
            _required_text(key,'outcome id')
            if type(price) not in (int,float) or not math.isfinite(price) or price <= 1: raise ObservationError('invalid price')
        if p['status']=='open' and (p['quote_at_claim'] is None or p['valid_until'] is None):
            raise ObservationError('open odds need quote time and expiry')
        if p['quote_at_claim'] is not None:
            p['quote_at_claim'] = _stamp(p['quote_at_claim'],'quote_at_claim')
            if p['quote_at_claim'] > record['received_at']: raise ObservationError('quote claim after receipt')
        if p['valid_until'] is not None:
            p['valid_until'] = _stamp(p['valid_until'],'valid_until')
            if p['quote_at_claim'] is not None and p['valid_until'] < p['quote_at_claim']: raise ObservationError('expiry before quote')
    elif kind == 'lineup':
        _keys(p, {'provider_team_id','announcement_status','players'})
        _required_text(p['provider_team_id'],'provider_team_id')
        if p['announcement_status'] not in ('confirmed','probable'): raise ObservationError('invalid lineup status')
        if not isinstance(p['players'],list) or not p['players']: raise ObservationError('empty lineup')
        seen=set()
        for player in p['players']:
            _keys(player,{'provider_player_id','role','order'})
            for key in ('provider_player_id','role'): _required_text(player[key],key)
            if player['provider_player_id'] in seen: raise ObservationError('duplicate player')
            seen.add(player['provider_player_id'])
            if player['order'] is not None and (type(player['order']) is not int or player['order'] < 1): raise ObservationError('invalid order')
        orders=[x['order'] for x in p['players'] if x['order'] is not None]
        if len(orders)!=len(set(orders)): raise ObservationError('duplicate order')
    elif kind == 'injury':
        _keys(p, {'provider_team_id','provider_player_id','status'})
        for key in p: _required_text(p[key],key)
    elif kind == 'substitution':
        _keys(p, {'provider_team_id','player_in_id','player_out_id'})
        for key in p: _required_text(p[key],key)
        if p['player_in_id']==p['player_out_id']: raise ObservationError('substitution identity conflict')
        if record['phase']!='inplay': raise ObservationError('substitution must be inplay')
    else:
        _keys(p, {'provider_product_id','settlement_scope','status','result','final_at'})
        _required_text(p['provider_product_id'],'provider_product_id'); _required_text(p['settlement_scope'],'settlement_scope')
        if p['status'] not in ('final','void','corrected'): raise ObservationError('invalid settlement status')
        p['final_at'] = _stamp(p['final_at'],'final_at')
        if p['final_at'] > record['received_at']: raise ObservationError('final after receipt')
        if p['status']!='void' and p['final_at'] < record['kickoff_at']: raise ObservationError('final before kickoff')
        if p['status']=='void' and p['result'] is not None: raise ObservationError('void cannot contain result')
        if p['status']!='void' and (not isinstance(p['result'],dict) or not p['result']): raise ObservationError('missing result')
    return record


class ObservationStore:
    """Caller-owned SQLite connection. Tables are isolated from existing production tables."""
    def __init__(self, connection: sqlite3.Connection):
        self.db=connection
        self.db.execute('''CREATE TABLE IF NOT EXISTS observation_event_v1 (
          provider TEXT NOT NULL, provider_game_id TEXT NOT NULL, kind TEXT NOT NULL,
          stream_id TEXT NOT NULL, revision_id TEXT NOT NULL, sequence INTEGER NOT NULL,
          canonical_game_id TEXT NOT NULL, received_at TEXT NOT NULL, body TEXT NOT NULL,
          raw BLOB NOT NULL, identity_digest TEXT NOT NULL, content_digest TEXT NOT NULL,
          PRIMARY KEY(provider,provider_game_id,kind,stream_id,revision_id),
          UNIQUE(provider,provider_game_id,kind,stream_id,sequence))''')
        self.db.execute('CREATE INDEX IF NOT EXISTS observation_asof_v1 ON observation_event_v1(canonical_game_id,received_at)')
        self.db.commit()

    def append(self, observation: Mapping[str,Any], raw: bytes) -> dict[str,Any]:
        rec=normalize(observation,raw)
        stream=(rec['provider'],rec['provider_game_id'],rec['kind'],rec['stream_id'])
        ident=_sha({k:v for k,v in rec.items() if k!='received_at'})
        content=_sha({'phase':rec['phase'],'payload':rec['payload']})
        self.db.execute('BEGIN IMMEDIATE')
        try:
            records=self.db.execute('''SELECT revision_id,sequence,canonical_game_id,received_at,body,identity_digest,content_digest
                FROM observation_event_v1 WHERE provider=? AND provider_game_id=? AND kind=? AND stream_id=?''',stream).fetchall()
            for old in records:
                old_record=json.loads(old[4])
                if old[2]!=rec['canonical_game_id'] or old_record['kickoff_at']!=rec['kickoff_at'] or old_record['provider_event_id']!=rec['provider_event_id']:
                    raise ObservationError('stream identity conflict')
                identity_fields={'odds':('provider_product_id','market','period','line','settlement_scope','outcome_ids'),
                                 'lineup':('provider_team_id',),'injury':('provider_team_id','provider_player_id'),
                                 'substitution':('provider_team_id',),'settlement':('provider_product_id','settlement_scope')}
                if any(old_record['payload'][k]!=rec['payload'][k] for k in identity_fields[rec['kind']]):
                    raise ObservationError('stream payload identity conflict')
                if old[0]==rec['provider_revision_id']:
                    if old[5]!=ident: raise ObservationError('revision content conflict')
                    if rec['received_at'] < old[3]: raise ObservationError('receipt order reversal')
                    self.db.commit(); return {'status':'duplicate','action':'none','first_received_at':old[3]}
                if old[1]==rec['sequence']: raise ObservationError('sequence conflict')
            if records and rec['received_at'] < max(r[3] for r in records): raise ObservationError('receipt order reversal')
            if rec['supersedes_revision_id'] is not None:
                prior=next((r for r in records if r[0]==rec['supersedes_revision_id']),None)
                if prior is None or prior[1]>=rec['sequence']: raise ObservationError('invalid correction reference')
            highest=max(records,key=lambda r:r[1]) if records else None
            is_newest=highest is None or rec['sequence']>highest[1]
            changed=is_newest and (highest is None or highest[6]!=content)
            self.db.execute('INSERT INTO observation_event_v1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (*stream,rec['provider_revision_id'],rec['sequence'],rec['canonical_game_id'],rec['received_at'],_json(rec),raw,ident,content))
            self.db.commit()
        except Exception:
            self.db.rollback(); raise
        action='refresh_freshness' if is_newest and not changed and rec['kind']!='settlement' else 'none'
        if changed:
            if rec['kind']=='settlement': action='settlement_only'
            elif rec['kind']=='odds' and (rec['payload']['status']!='open' or rec['payload']['valid_until'] < rec['received_at']): action='invalidate'
            else: action='recompute_prediction'
        if is_newest and rec['kind']!='settlement' and rec['phase']=='pregame' and _time(rec['received_at'],'received_at') > _time(rec['kickoff_at'],'kickoff_at')-timedelta(minutes=30):
            action='pregame_frozen'
        return {'status':'appended' if is_newest else 'out_of_order','action':action,'first_received_at':rec['received_at']}

    def raw_revision(self, provider: str, provider_game_id: str, kind: str, stream_id: str, revision_id: str) -> bytes:
        """Return recorded raw bytes only after rechecking the original source hash."""
        row=self.db.execute("SELECT raw,body FROM observation_event_v1 WHERE provider=? AND provider_game_id=? AND kind=? AND stream_id=? AND revision_id=?",
                            (provider,provider_game_id,kind,stream_id,revision_id)).fetchone()
        if row is None: raise ObservationError('unknown revision')
        raw, body=row
        if hashlib.sha256(raw).hexdigest()!=json.loads(body)['source_sha256']: raise ObservationError('stored raw hash mismatch')
        return raw

    def snapshot(self, canonical_game_id: str, decision_at: str, phase: str, kickoff_at: str,
                 max_age_seconds: Mapping[str,int]) -> dict[str,Any]:
        """Return latest received revisions and usability, never fallback to stale prices."""
        _required_text(canonical_game_id,'canonical_game_id')
        decision=_stamp(decision_at,'decision_at'); kickoff=_stamp(kickoff_at,'kickoff_at')
        if phase not in PHASES: raise ObservationError('invalid phase')
        if phase=='pregame' and _time(decision,'decision_at') > _time(kickoff,'kickoff_at')-timedelta(minutes=30): raise ObservationError('pregame cutoff exceeded')
        if phase=='inplay' and decision < kickoff: raise ObservationError('inplay before kickoff')
        if not isinstance(max_age_seconds,Mapping) or set(max_age_seconds)!=(KINDS-{'settlement'}): raise ObservationError('explicit max age for each input kind required')
        if any(type(v) is not int or v<0 for v in max_age_seconds.values()): raise ObservationError('invalid max age')
        rows=self.db.execute('''SELECT provider,provider_game_id,kind,stream_id,sequence,body,content_digest FROM observation_event_v1
            WHERE canonical_game_id=? AND received_at<=? AND kind!='settlement' ''',(canonical_game_id,decision)).fetchall()
        latest={}
        for provider,game,kind,stream,seq,body,digest in rows:
            rec=json.loads(body)
            if rec['kickoff_at']!=kickoff: raise ObservationError('game kickoff conflict')
            if phase=='pregame' and rec['phase']!='pregame': continue
            key=(provider,game,kind,stream)
            if key not in latest or seq>latest[key][0]: latest[key]=(seq,rec,digest)
        entries=[]
        for _,(_,rec,digest) in sorted(latest.items()):
            age=(_time(decision,'decision_at')-_time(rec['received_at'],'received_at')).total_seconds()
            reason=None
            if age>max_age_seconds[rec['kind']]: reason='stale'
            if rec['kind']=='odds':
                if rec['payload']['status']!='open': reason=rec['payload']['status']
                else:
                    quote_age=(_time(decision,'decision_at')-_time(rec['payload']['quote_at_claim'],'quote_at_claim')).total_seconds()
                    if quote_age>max_age_seconds['odds']: reason='stale'
                    if decision>rec['payload']['valid_until']: reason='expired'
            if rec['phase']=='pregame' and rec['received_at']>=kickoff: reason='late_pregame'
            entries.append({'observation':rec,'content_sha256':digest,'usable':reason is None,'reason':reason})
        envelope={'schema_version':SCHEMA,'canonical_game_id':canonical_game_id,'kickoff_at':kickoff,
                  'decision_at':decision,'phase':phase,'max_age_seconds':dict(max_age_seconds),'entries':entries,
                  'purpose':'prediction_recalculation_only'}
        envelope['provenance_sha256']=_sha(envelope)
        return envelope
