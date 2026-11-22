"""Offline development diagnostic, never production promotion or fresh holdout."""
import argparse
import hashlib
import importlib.util
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

POLICIES = ('model', 'tipster', 'fixed25', 'disagreement50', 'consensus50', 'past_brier')


def stamp(s):
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t


def validate(rows):
    seen = set()
    for r in rows:
        if r['event'] in seen:
            raise ValueError('Duplicate game')
        seen.add(r['event'])
        if not stamp(r['snapshot_at']) <= stamp(r['at']) <= stamp(r['kickoff'])-timedelta(minutes=30):
            raise ValueError('Not pregame')
        if stamp(r['settled_at']) < stamp(r['kickoff']):
            raise ValueError('Result before game')
        if type(r['home_won']) is not bool or r['authors'] < 3:
            raise ValueError('Invalid result or author count')
        for p in r['arms'].values():
            if not math.isfinite(p) or not 0 <= p <= 1:
                raise ValueError('Invalid probability')
        if len(r['odds']) != 2 or any(not math.isfinite(o) or o <= 1 for o in r['odds']):
            raise ValueError('Invalid odds')


def weight(r, policy, history):
    p, q = r['arms']['production'], r['arms']['tipster']
    if policy == 'model':
        return 0.
    if policy == 'tipster':
        return 1.
    if policy == 'fixed25':
        return .25
    if policy == 'disagreement50':
        return .5 if (p-.5)*(q-.5) < 0 and abs(p-q) >= .2 else .25
    if policy == 'consensus50':
        return .5 if r['authors'] >= 5 and max(q, 1-q) >= .75 else .25
    if policy != 'past_brier':
        raise ValueError('Unknown policy')
    # No current/future outcome or unobserved result can select the weight.
    past = [x for x in history if x['date'] < r['date'] and stamp(x['settled_at']) < stamp(r['at'])]
    if len(past) < 10:
        return .25
    def loss(w):
        return sum(((1-w)*x['arms']['production']+w*x['arms']['tipster']-x['home_won'])**2 for x in past)/len(past)
    # Switch only on a 0.01 Brier advantage; fixed25 wins ties.
    best = min((0., .25, .5), key=lambda w: (loss(w), abs(w-.25)))
    return best if loss(.25)-loss(best) >= .01 else .25


def predict(rows, policy):
    return [dict(r, tipster_weight=(w := weight(r, policy, rows)),
                 probability=(1-w)*r['arms']['production']+w*r['arms']['tipster']) for r in rows]


def metrics(rows):
    if not rows:
        return dict(n=0, wins=0, roi_pct=None, brier=None)
    wins = net = error = 0
    for r in rows:
        home = r['probability'] >= .5
        hit = home == r['home_won']
        wins += hit
        net += r['odds'][0 if home else 1]-1 if hit else -1
        error += (r['probability']-r['home_won'])**2
    return dict(n=len(rows), wins=wins, roi_pct=100*net/len(rows), brier=error/len(rows))


def run(rows, replay):
    validate(rows)
    result = {}
    for policy in POLICIES:
        pred = predict(rows, policy)
        choices = []
        for r in pred:
            home = r['probability'] >= .5
            choices.append(dict(id=r['event_id'], event=r['event'], author=policy,
                side=r['home'] if home else r['away'], date=r['date'],
                observed=replay.stamp(r['at']), kickoff=replay.stamp(r['kickoff']),
                odds=r['odds'][0 if home else 1], outcome='W' if home == r['home_won'] else 'L',
                settled=replay.stamp(r['settled_at'])))
        cutoff = max((x['settled'] for x in choices), default=None)
        result[policy] = dict(single=metrics(pred),
            daily={d: metrics([r for r in pred if r['date'] == d]) for d in sorted({r['date'] for r in pred})},
            two_leg={str(w): replay.simulate(choices, cutoff, 'lower_odds', w) for w in (2, 3)} if choices else {},
            decisions=[dict(event=r['event'], tipster_weight=r['tipster_weight'], probability=r['probability']) for r in pred])
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--replay', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    s = importlib.util.spec_from_file_location('paper_replay', a.replay)
    replay = importlib.util.module_from_spec(s)
    s.loader.exec_module(replay)
    rows = json.loads(a.input.read_text(encoding='utf8'))['paired_rows']
    report = dict(status='development_only_not_independent', monthly_loss_limit_krw=50000,
        hashes={str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in (a.input, a.replay, Path(__file__))},
        limitations=['Previously examined 28-game sample; no fresh test or promotion.',
            'Publisher results, not independently verified official results.',
            'Resolved common subset; missingness and selection bias remain.',
            'Noon execution price availability not proven. Two-leg payout reuses prior protocol.',
            'No author-level reliability, injury, lineup or odds movement in these paired rows.',
            'Monthly loss cap applies separately to each counterfactual account; not a combined wagering budget.'],
        policies=run(rows, replay))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    for name, r in report['policies'].items():
        print(name, json.dumps(dict(single=r['single'], two_leg={w:{k:v for k,v in x.items() if k != 'ticket_log'} for w,x in r['two_leg'].items()})))


if __name__ == '__main__':
    main()
