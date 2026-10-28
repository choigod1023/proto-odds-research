"""Offline replay of captured vote inputs; no outcome/ROI inference or network."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from fixed_tipster_hybrid import context


def replay(path):
    data = json.loads(Path(path).read_text(encoding='utf8'))
    expected = data['public_votes_only_comparison']
    old, new = data['existing_public_observations'], data['new_first_observations']
    at = expected['as_of']
    before, after, changes = 0, 0, []
    for game in data['input_fixture_identities']:
        b, a = context(game, old, at, at), context(game, old+new, at, at)
        before += bool(b and b['status'] == 'applied')
        after += bool(a and a['status'] == 'applied')
        if b and a and (b.get('selected_side'), b['authors']) != (a.get('selected_side'), a['authors']):
            changes.append(dict(home=game['home'], away=game['away'], kickoff=game['date'],
                before_side=b.get('selected_side'), before_authors=b['authors'],
                after_side=a.get('selected_side'), after_authors=a['authors'], votes=a.get('vote_counts')))
    assert (before, after, changes) == (expected['applied_before'], expected['applied_after'], expected['changes'])
    return dict(applied_before=before, applied_after=after, changes=changes,
                profitability_status=data['profitability_status'])


if __name__ == '__main__':
    path = ROOT / 'docs/research/evidence/2026-10-28-diverse-tipsters.json'
    print(json.dumps(replay(path), ensure_ascii=False, indent=2))
