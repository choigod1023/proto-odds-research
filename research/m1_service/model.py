"""Inference-only M1/M0 replay from pinned coefficients; no fitting or HTTP."""
from copy import deepcopy
from datetime import date
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import skellam

BUNDLE_SHA256 = '4bc13aaceb3b04f0a8c413034ea033b9d9eab1fcca156ff09c73e4aab8b3f50b'
PATH = Path(__file__).with_name('frozen-models.json')
TEAM_NAMES = {'HH': '한화', 'KT': 'KT', 'SS': '삼성', 'OB': '두산', 'LG': 'LG',
              'WO': '키움', 'HT': 'KIA', 'LT': '롯데', 'NC': 'NC', 'SK': 'SSG'}
FEATURES = {'park_factor'} | {s+'_'+k for s in ('home', 'away') for k in
                             ('team_strength', 'rest_days', 'lineup_strength', 'starter_strength', 'bullpen_load')}


@lru_cache(maxsize=1)
def _bundle():
    raw = PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != BUNDLE_SHA256:
        raise ValueError('frozen M1 bundle hash mismatch')
    data = json.loads(raw)
    if data['promoted'] or data['calibrated_for_ev_selection']:
        raise ValueError('research bundle cannot self-promote')
    return data


def metadata():
    data = _bundle()
    return {'bundle_sha256': BUNDLE_SHA256, 'sources': deepcopy(data['sources']),
            'models': list(data['models']), 'calibrated_for_ev_selection': False,
            'all_coefficients_preserved_by': max(s['first_preserved_commit_at'] for s in data['sources'].values()),
            'affects_selection': False, 'affects_probability': False, 'promoted': False}


def _team(value):
    name = TEAM_NAMES.get(value, value)
    if name not in TEAM_NAMES.values():
        raise ValueError('unknown official or frozen team ID')
    return name


def _validate_row(row):
    if type(row['season']) is not int or row['season'] <= 2025 or date.fromisoformat(row['event_date']).year != row['season']:
        raise ValueError('frozen 2023-25 training cannot predict a training-season target')
    if set(row['features']) != FEATURES or _team(row['home_id']) == _team(row['away_id']):
        raise ValueError('frozen feature set and distinct teams required')
    if row['features']['park_factor']['value'] is not None:
        raise ValueError('frozen normalized park_factor must be null')
    for value in row['features'].values():
        x = value['value']
        if x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)):
            raise ValueError('nonfinite or nonnumeric feature; null is explicit missing')
    for s in ('home', 'away'):
        roster = row['roster'][s]
        hitters = roster['hitters']
        if hitters is not None and (not isinstance(hitters, list)
                                    or len(hitters) != 9 or len(set(hitters)) != len(hitters)
                                    or any(not isinstance(x, str) or not x for x in hitters)):
            raise ValueError('invalid ordered hitter IDs')
        starter = roster['starter']
        if starter is not None and (not isinstance(starter, str) or not starter):
            raise ValueError('invalid starter ID')
    _team(row['home_id']); _team(row['away_id'])
    home, away = row['roster']['home'], row['roster']['away']
    if (set(home['hitters'] or []) & set(away['hitters'] or [])
            or home['starter'] is not None and home['starter'] == away['starter']):
        raise ValueError('opposing player identity collision')


def _vector(row, side, model):
    spec = model['spec']; opponent = 'away' if side == 'home' else 'home'
    own, other = _team(row[side+'_id']), _team(row[opponent+'_id'])
    names = [side+'_team_strength', opponent+'_team_strength', side+'_rest_days',
             opponent+'_rest_days', 'park_factor', side+'_lineup_strength',
             opponent+'_starter_strength', opponent+'_bullpen_load']
    values = [row['features'][name]['value'] for name in names]
    missing = [float(x is None) for x in values]
    norm = [0.0 if x is None else (x-m)/scale for x, m, scale in zip(values, spec['mean'], spec['scale'])]
    base = [1., float(side == 'home')] + [float(own == t) for t in spec['teams']]
    base += [float(other == t) for t in spec['teams']] + norm + missing
    hmap = {v: i for i, v in enumerate(spec['hitters'])}
    smap = {v: i for i, v in enumerate(spec['starters'])}
    line = row['roster'][side]['hitters']; starter = row['roster'][opponent]['starter']
    flags = [float(line is None), sum(p not in hmap for p in (line or []))/9,
             float(starter is None), float(starter is not None and starter not in smap)]
    if model['family'] == 'M1':
        base += flags
    if len(base) != model['nbase']:
        raise ValueError('frozen design dimension differs')
    if model['family'] == 'M1':
        exposure = [0.] * (len(hmap)+len(smap))
        for pos, player in enumerate(line or []):
            if player in hmap:
                exposure[hmap[player]] += _bundle()['batting_exposure'][pos]
        if starter in smap:
            exposure[len(hmap)+smap[starter]] = 1.
        base += exposure
    if len(base) != len(model['beta']):
        raise ValueError('frozen coefficient dimension differs')
    return np.asarray(base), flags


def forecast(row):
    """Low-level retrospective or live inference; source eligibility is separate.

    Missing statistics follow the frozen fit's mean+indicator transform. Unknown
    player identities have neutral trained role effect and explicit flags.
    """
    _validate_row(row)
    output = {}
    for name, model in _bundle()['models'].items():
        rates, coverage = [], {}
        for side in ('home', 'away'):
            vector, flags = _vector(row, side, model)
            eta = float(vector @ np.asarray(model['beta']))
            if not math.isfinite(eta) or abs(eta) > 20:
                raise ValueError('unstable frozen run rate')
            rates.append(math.exp(eta)); coverage[side] = flags
        h, a = rates
        p = np.array([skellam.sf(0, h, a), skellam.pmf(0, h, a), skellam.cdf(-1, h, a)])
        if not np.isfinite(p).all() or (p < 0).any() or p.sum() <= 0:
            raise ValueError('invalid frozen score distribution')
        p /= p.sum(); decisive = p[0]+p[2]
        if decisive <= 0:
            raise ValueError('no decisive probability')
        output[name] = {'fixture_id': row['fixture_id'], 'event_date': row['event_date'],
                        'home_rate': h, 'away_rate': a,
                        'probabilities_3way': dict(zip(('home', 'draw', 'away'), map(float, p))),
                        'probabilities_2way': {'home': float(p[0]/decisive), 'away': float(p[2]/decisive)},
                        'roster_coverage': coverage, 'probability_calibrated': False,
                        'affects_selection': False, 'affects_probability': False,
                        'model_bundle_sha256': BUNDLE_SHA256}
    return output
