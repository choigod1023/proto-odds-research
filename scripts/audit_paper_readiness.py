"""Read-only production evidence audit and local settlement dry-run; no journal writes."""
import argparse
import base64
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen

from scripts.run_forward_paper import advance, identity, settlement_row, ticket_summary
from scripts.audit_recommendation_shadow import KST


def audit(events, doc, now):
    history = list(doc['recommendation_history'].values())
    counts = Counter()
    for e in events:
        if e['type'] != 'bet':
            continue
        for leg in e['legs']:
            counts['legs'] += 1
            counts['old_exact_match'] += sum(identity(r) == leg for r in history)
            counts['safe_match'] += settlement_row(leg, history, now) is not None
    after, added = advance(events, doc, now, 'local-dry-run')
    accounts = sorted({e['account'] for e in events if e['type'] == 'bet'})
    return dict(at=now.isoformat(), source_generated_at=doc['generated_at'],
        production_action='none', events=dict(Counter(e['type'] for e in events)), matches=dict(counts),
        dry_run_added=dict(Counter(e['type'] for e in added)),
        before={a:ticket_summary(events,a) for a in accounts},
        after={a:ticket_summary(after,a) for a in accounts},
        hybrid_promotion='blocked', blockers=[
            'Existing accounts are interest-pick policies, not a paired hybrid forward trial.',
            'Historical 28-game development sample is not independent prospective evidence.',
            'Need frozen candidate/protocol and same-time model/tipster/odds provenance.',
            'Need verified official results and two-leg ROI/risk review before approval.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    # Create a unique local evidence directory; never overwrite earlier observations.
    a.output.mkdir(parents=True,exist_ok=False)
    raw=subprocess.check_output(['gh','api',
        'repos/choigod1023/proto-odds-research/contents/paper/journal.json?ref=codex/paper-state-v1'])
    envelope=json.loads(raw); journal=base64.b64decode(envelope['content'])
    with urlopen('https://proto-odds-collector.fly.dev/api/today-recommendations',timeout=25) as response:
        source=response.read()
    now=datetime.now(KST)
    report=audit(json.loads(journal),json.loads(source),now)
    report['journal_blob_sha']=envelope['sha']
    for name,data in [('journal.json',journal),('source.json',source)]:
        with (a.output/name).open('xb') as f:f.write(data)
        report[name+'_sha256']=hashlib.sha256(data).hexdigest()
    with (a.output/'report.json').open('x',encoding='utf8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
