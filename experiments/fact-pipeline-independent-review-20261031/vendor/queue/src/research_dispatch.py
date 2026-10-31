"""Local research-only new-evidence queue. No network, model fitting or promotion.

Adapter eligibility is an assertion, not independent proof of publication/receipt.
Thread destinations belong to the operator registry, never to source documents.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

LEAGUES = frozenset(('kleague1', 'kleague2', 'kbo', 'npb', 'mlb', 'kbl',
                     'nba', 'vleague_m', 'vleague_w', 'football_other'))
SHA = re.compile(r'^[a-f0-9]{64}$')
SCHEMA = 'league-research-manifest-v1'
_VOLATILE = frozenset(('received_at', 'observed_at', 'published_at', 'generated_at',
                       'captured_at', 'raw_sha256', 'fact_id', 'input_id', 'revision'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()


def _semantic(value):
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        # Fact and evidence order has no semantic meaning. Outcome/price vectors do.
        return [_semantic(v) for v in value]
    return value


def build_manifest(adapter_inputs):
    """Build a content manifest; receipt-only/raw-HTML-only changes do not wake work.

    A validated adapter's semantic hash also covers fixture/player identity omitted
    from its display features. We require it, but do not treat a hash as certification.
    """
    cases = {}
    skipped = 0
    for item in adapter_inputs:
        if not isinstance(item, dict) or item.get('schema_version') != 'fact-delta-inputs-v1':
            raise ValueError('unsupported adapter input')
        league = item.get('league_id')
        if league not in LEAGUES:
            raise ValueError('unknown league')
        if item.get('research_eligible') is not True or item.get('status') != 'ready':
            skipped += 1
            continue
        if not SHA.fullmatch(str(item.get('semantic_sha256', ''))):
            raise ValueError('adapter semantic hash required')
        for key in ('game_id', 'market_id'):
            if not isinstance(item.get(key), str) or not item[key] or len(item[key]) > 256:
                raise ValueError('invalid case identity')
        if not isinstance(item.get('features'), dict) or not item['features']:
            raise ValueError('ready input needs features')
        # Deliberately omit source text, prompt, destination and filesystem paths.
        features = _semantic(item['features'])
        if isinstance(features.get('facts'), list):
            features['facts'].sort(key=canonical)
        identity = [league, item['game_id'], item['market_id']]
        material = {'identity': identity, 'adapter_sha256': item['semantic_sha256'], 'features': features}
        key = digest(identity)
        entry = {'case_id': key, 'league_id': league, 'game_id': item['game_id'],
                 'market_id': item['market_id'], 'semantic_sha256': digest(material),
                 'input': deepcopy(item)}
        if key in cases and cases[key]['semantic_sha256'] != entry['semantic_sha256']:
            raise ValueError('conflicting revisions for one case; select a revision upstream')
        cases.setdefault(key, entry)
    entries = sorted(cases.values(), key=lambda x: x['case_id'])
    content = [{'case_id': e['case_id'], 'semantic_sha256': e['semantic_sha256']} for e in entries]
    return {'schema_version': SCHEMA, 'semantic_sha256': digest(content),
            'cases': entries, 'skipped_ineligible': skipped}


def validate_manifest(manifest):
    if manifest.get('schema_version') != SCHEMA or not isinstance(manifest.get('cases'), list):
        raise ValueError('unsupported manifest')
    rebuilt = build_manifest([e['input'] for e in manifest['cases']])
    if canonical(rebuilt['cases']) != canonical(manifest['cases']) or rebuilt['semantic_sha256'] != manifest.get('semantic_sha256'):
        raise ValueError('manifest integrity mismatch')
    return rebuilt


def _write(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf8', newline='\n') as f:
            f.write(canonical(value) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _locked(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'queue.lock'
    try:
        handle = lock.open('x', encoding='utf8')
    except FileExistsError as exc:
        raise RuntimeError('queue busy; stale lock requires operator review') from exc
    try:
        handle.write(str(os.getpid()))
        handle.close()
        yield root
    finally:
        handle.close()
        lock.unlink()


def _read(root):
    path = root / 'queue.json'
    if not path.exists():
        return {'schema_version': 'league-research-queue-v1', 'seen': {}, 'jobs': []}
    queue = json.loads(path.read_text(encoding='utf8'))
    if queue.get('schema_version') != 'league-research-queue-v1':
        raise ValueError('unsupported queue')
    return queue


def publish(root, manifest):
    """Save immutable full inputs before adding pending jobs; source never routes work."""
    manifest = validate_manifest(manifest)
    with _locked(root) as root:
        queue = _read(root)
        grouped = {}
        for case in manifest['cases']:
            signature = case['semantic_sha256']
            if signature not in queue['seen'].get(case['case_id'], []):
                grouped.setdefault(case['league_id'], []).append(case)
        created = []
        for league, cases in sorted(grouped.items()):
            payload = {'schema_version': SCHEMA, 'league_id': league, 'cases': cases}
            blob_sha = digest(payload)
            event = digest({'league_id': league, 'cases': sorted(c['semantic_sha256'] for c in cases)})
            snapshot_dir = root / 'inputs'
            snapshot_dir.mkdir(exist_ok=True)
            snapshot = snapshot_dir / (blob_sha + '.json')
            if snapshot.exists():
                if digest(json.loads(snapshot.read_text(encoding='utf8'))) != blob_sha:
                    raise ValueError('existing immutable snapshot corrupted')
            else:
                _write(snapshot, payload)
            job = {'event_id': event, 'league_id': league, 'input_sha256': blob_sha,
                   'input_path': 'inputs/' + blob_sha + '.json', 'state': 'pending'}
            queue['jobs'].append(job)
            created.append(deepcopy(job))
            for case in cases:
                queue['seen'].setdefault(case['case_id'], []).append(case['semantic_sha256'])
        _write(root / 'queue.json', queue)
        return created


def load_job_input(root, job):
    """Resolve only our own content-addressed filename; reject traversal/symlinks."""
    root = Path(root).resolve()
    sha = job.get('input_sha256', '')
    if not SHA.fullmatch(sha) or job.get('input_path') != 'inputs/' + sha + '.json':
        raise ValueError('unsafe input path')
    path = root / 'inputs' / (sha + '.json')
    if path.resolve().parent != root / 'inputs' or path.is_symlink():
        raise ValueError('unsafe input target')
    payload = json.loads(path.read_text(encoding='utf8'))
    if digest(payload) != sha or payload.get('league_id') != job['league_id']:
        raise ValueError('snapshot integrity mismatch')
    if any(c['league_id'] != job['league_id'] for c in payload['cases']):
        raise ValueError('cross-league input')
    return payload


def claim(root, *, limit=3):
    """Claim at most three leagues; an unacknowledged claim remains busy indefinitely.

    Never expire/retry automatically: native message delivery may have succeeded
    before its response was lost. Operator must inspect destination before release.
    """
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('limit must be 1..3')
    with _locked(root) as root:
        queue = _read(root)
        busy = {j['league_id'] for j in queue['jobs'] if j['state'] in ('claimed', 'dispatched', 'uncertain')}
        selected = []
        for job in queue['jobs']:
            if job['state'] != 'pending' or job['league_id'] in busy:
                continue
            load_job_input(root, job)
            job['state'] = 'claimed'
            busy.add(job['league_id'])
            selected.append(deepcopy(job))
            if len(selected) == limit:
                break
        _write(root / 'queue.json', queue)
        return selected


def transition(root, event_id, state, *, receipt=None, operator_checked=False):
    allowed = {'claimed': {'dispatched', 'uncertain', 'pending'},
               'uncertain': {'dispatched', 'pending'}, 'dispatched': {'completed'}}
    with _locked(root) as root:
        queue = _read(root)
        matches = [j for j in queue['jobs'] if j['event_id'] == event_id]
        if len(matches) != 1:
            raise ValueError('event not unique')
        job = matches[0]
        if state not in allowed.get(job['state'], set()):
            raise ValueError('invalid transition')
        if (state == 'pending' or job['state'] == 'uncertain') and operator_checked is not True:
            raise ValueError('destination inspection required; no blind retries')
        if state == 'completed':
            expected = {'event_id': event_id, 'input_sha256': job['input_sha256'], 'status': 'completed'}
            if not isinstance(receipt, dict) or any(receipt.get(k) != v for k,v in expected.items()):
                raise ValueError('matching completion receipt required')
        job['state'] = state
        _write(root / 'queue.json', queue)
        return deepcopy(job)


def status(root):
    with _locked(root) as root:
        return deepcopy(_read(root)['jobs'])
