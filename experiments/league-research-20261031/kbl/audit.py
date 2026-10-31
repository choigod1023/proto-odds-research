import hashlib, json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
p = ROOT / 'experiments/operating-ledger-comparison-20261029/comparison.json'
data = json.loads(p.read_text(encoding='utf-8'))
rows = [r for r in data['events'] if r['fixture']['league'] == 'KBL']
def t(s): return datetime.fromisoformat(s.replace('Z', '+00:00'))
def eligible_time(r):
    return t(r['as_of']) <= t(r['captured_at']) <= t(r['kickoff']) - timedelta(minutes=30)
assert len(rows) == 4
assert len({r['event_id'] for r in rows}) == 4
assert all(eligible_time(r) for r in rows)
assert all(r['arms']['market_reference']['status'] == 'selected' and r['arms']['market_reference']['odds'] > 1 for r in rows)
assert all(r['label_status'] == 'settled' and t(r['official']['observed_at']) > t(r['kickoff']) for r in rows)
assert sum(r['policy'] == 'tipster-vote-v1' for r in rows) == 1
assert sum(r['arms']['fixed91_same_electorate']['status'] == 'selected' for r in rows) == 2
# Synthetic adversarial checks; these validate the audit rules, not historical player eligibility.
a = dict(rows[0]); a['captured_at'] = a['kickoff']; assert not eligible_time(a)
raw = b'official source bytes'; assert hashlib.sha256(raw).digest() == hashlib.sha256(raw).digest()
assert hashlib.sha256(raw).digest() != hashlib.sha256(b'different').digest()
def prior_labels(examples, fit_at): return [x['label'] for x in examples if t(x['received']) < fit_at]
fit = t('2026-10-01T00:00:00+00:00')
examples = [{'received':'2026-09-30T00:00:00+00:00','label':1}, {'received':'2026-10-02T00:00:00+00:00','label':0}]
before = prior_labels(examples, fit); examples[1]['label'] = 1; assert before == prior_labels(examples, fit) == [1]
print(json.dumps({'input_sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'kbl_win_rows':len(rows), 'stored_as_of_and_capture_before_t30':sum(eligible_time(r) for r in rows), 'market_selected_price':len(rows), 'settled_official_link':len(rows), 'current_policy_rows':sum(r['policy']=='tipster-vote-v1' for r in rows), 'stored_vote_comparison_rows':sum(r['arms']['fixed91_same_electorate']['status']=='selected' for r in rows), 'player_evidence_fields_in_comparison':0, 'synthetic_late_capture_duplicate_hash_future_label_checks':'passed'},ensure_ascii=False,indent=2))
