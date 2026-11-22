"""Independent reference for registered paired-missingness correction only."""
import hashlib
import json
import subprocess
from pathlib import Path
import numpy as np
import audit_frozen as v1

REPO=Path('C:/Users/user/Documents/Codex/kbnext')
COMMIT='ebe25eb71b1fdde76bfeb55938c3df26c5cc3e18'
PATH=Path('experiments/kbo-player-effects-20261103/implementation-correction-1.json')
SHA='c368b227534310a67a46a7f80b510867ae14e3bb936d3dd8eed03f085d9f06f6'

def correction_contract():
    raw=subprocess.check_output(['git','show',f'{COMMIT}:{PATH.as_posix()}'],cwd=REPO)
    if hashlib.sha256(raw).hexdigest()!=SHA:raise ValueError('correction contract mismatch')
    c=json.loads(raw)
    if c['no_new_candidate_family'] is not True or c['no_new_penalty_grid'] is not True:raise ValueError('correction scope mismatch')
    return c

def design(row,spec):
    """Keep v1 M2 contrasts and insert exactly 8+4 nonnegative sums before player IDs."""
    original,flags=v1.design_one(row,spec,'M2');nbase=1+len(spec['teams'])+16+4
    home_missing=v1.vector(row,'home',spec)[1];away_missing=v1.vector(row,'away',spec)[1]
    sums=np.r_[home_missing+away_missing,np.asarray(flags[0])+np.asarray(flags[1])]
    if len(sums)!=12 or np.any(sums<0) or np.any(sums>2):raise ValueError('invalid quality sums')
    return np.r_[original[0,:nbase],sums,original[0,nbase:]],original[0],sums
