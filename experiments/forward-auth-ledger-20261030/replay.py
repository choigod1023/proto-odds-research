"""Offline fixed-protocol audit/replay. No network or production imports."""
import argparse
import base64
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist
import sys

import numpy as np
from ledger import (Ledger, InvalidLedger, authenticated_freezes, canonical,
                    checkpoint_message, digest, stamp, verify_chain, verify_checkpoint)
import independent

HERE = Path(__file__).resolve().parent
MODELS = ("market", "market90_vote10")


def write_json(path, value):
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False).encode("utf8")+b"\n")


def split_dates(rows, date_field):
    days = sorted({r[date_field] for r in rows})
    cuts = (int(len(days)*.6), int(len(days)*.8))
    sets = [set(days[:cuts[0]]), set(days[cuts[0]:cuts[1]]), set(days[cuts[1]:])]
    return {name:[r for r in rows if r[date_field] in dates]
            for name,dates in zip(("train", "validation", "test"), sets)}


def frozen_predictions(events):
    """Projection of original predeclared market/control picks, no label access/fitting."""
    return dict(training_parameters={"fitted": False, "candidate": "market-control-only"},
                predictions=[{k:deepcopy(e[k]) for k in ("fixture_id", "order", "prices", "picks", "vote_counts")} for e in events])


def settlement(events, model):
    results = []
    for e in events:
        pick = e["picks"][model]
        if pick is None:
            continue
        hit = int(pick == e["winner"])
        price = e["prices"][e["order"].index(pick)]
        gross = int((Decimal(str(price))*10000).to_integral_value(rounding=ROUND_DOWN)) if hit else 0
        results.append((e["fixture_id"], hit, gross-10000))
    return results


def describe(events, model):
    results = settlement(events, model)
    n = len(results)
    wins = sum(r[1] for r in results)
    net = sum(r[2] for r in results)
    return dict(n=n, wins=wins, profit_krw=net, stake_krw=10000,
                accuracy=wins/n if n else None, roi=net/(10000*n) if n else None,
                coverage=n/len(events) if events else None,
                independent_integer_agreement=results == independent.settle(events, model))


def paired(events, candidate, control):
    if not events:
        return dict(n=0, accuracy_difference=None, roi_difference=None, ci=None)
    a = settlement(events, candidate)
    b = settlement(events, control)
    grouped = defaultdict(lambda: np.zeros(3, dtype=np.int64))
    for e,x,y in zip(events,a,b):
        d = datetime.fromisoformat(e["evaluation_date"])
        week = (d-timedelta(days=d.weekday())).date().isoformat()
        grouped[week] += [1, x[1]-y[1], x[2]-y[2]]
    groups = np.array([grouped[k] for k in sorted(grouped)])
    samples = np.random.default_rng(20261030).integers(0, len(groups), size=(2000, len(groups)))
    totals = groups[samples].sum(axis=1)
    draws = np.column_stack((totals[:,1]/totals[:,0],totals[:,2]/(10000*totals[:,0])))
    secondary = independent.weekly_metrics(events, candidate, control, samples.tolist())
    assert draws.tolist() == secondary, "independent weekly bootstrap disagreed"
    ci = np.quantile(draws,[.025,.975],axis=0)
    return dict(n=len(events), observed_weeks=len(groups), changed_picks=sum(e["picks"][candidate] != e["picks"][control] for e in events),
                accuracy_difference=sum(x[1]-y[1] for x,y in zip(a,b))/len(events),
                roi_difference=sum(x[2]-y[2] for x,y in zip(a,b))/(10000*len(events)),
                accuracy_ci95=ci[:,0].tolist(), roi_ci95=ci[:,1].tolist(),
                independent_bootstrap_exact_agreement=True, bootstrap_repetitions=2000,
                bonferroni_family_size=1, adjustment="sole preregistered ledger rule; descriptive controls, no new predictor")


def audit_timed(rows):
    leagues = defaultdict(lambda: dict(revisions=0, fixtures=set(), internal_t30=0, internal_fixtures=set(), clocks=[],
                                      certified_revisions=0, certified_fixtures=0))
    reasons = Counter()
    output = []
    valid_rows = []
    for r in rows:
        league = r["league"] or "UNKNOWN"
        g = leagues[league]
        g["revisions"] += 1
        g["fixtures"].add(r["event_id"])
        g["clocks"].append(stamp(r["captured_at"]))
        try:
            cutoff = stamp(r["kickoff"])-timedelta(minutes=30)
            internally = all(stamp(r[k]) <= cutoff for k in ("captured_at", "as_of", "market_observed_at"))
        except InvalidLedger:
            internally = False
        if internally:
            g["internal_t30"] += 1
            g["internal_fixtures"].add(r["event_id"])
        else:
            reasons["not_all_internal_clocks_at_or_before_T30"] += 1
        if not r.get("receipt_checkpoint"):
            reasons["missing_signed_external_checkpoint"] += 1
        if not r.get("source_sha256"):
            reasons["missing_original_raw_response_sha256"] += 1
        # Never manufacture a signature from an internal availability flag.
        assert not r.get("receipt_checkpoint") and not r.get("source_sha256")
        projection = dict(snapshot_id=r["snapshot_id"], event_id=r["event_id"], league=league,
                          kickoff=r["kickoff"], received_at=r["captured_at"], source_kind="retrospective_archive",
                          archive_row_sha256=digest(r), original_raw_source_sha256=None,
                          odds=[c["odds"] for c in r["choices"]],
                          probabilities=[{k:v for k,v in c.get("scores",{}).items() if k == "market"} for c in r["choices"]],
                          choices=[{**{k:c.get(k) for k in ('offer_id','market','label','selection','line','issue_year','round','game_no','odds')},
                                    'probabilities':{k:v for k,v in c.get('scores',{}).items() if k == 'market'}} for c in r['choices']],
                          votes=None, votes_status="not_present_not_inferred_from_score",
                          internal_t30=internally, externally_authenticated=False)
        output.append(projection)
        if r.get("kickoff"):
            valid_rows.append(dict(event_id=r["event_id"], date=stamp(r["kickoff"]).date().isoformat()))
    for g in leagues.values():
        elapsed = (max(g["clocks"])-min(g["clocks"])).total_seconds()/(7*86400)
        g.update(fixtures=len(g["fixtures"]), internal_t30_fixtures=len(g.pop("internal_fixtures")),
                 observation_start=min(g["clocks"]).isoformat(), observation_end=max(g["clocks"]).isoformat(),
                 elapsed_weeks=elapsed, authenticated_fixtures_per_week=0 if elapsed > 0 else None,
                 weeks_for_3pp80=None, weeks_reason="zero authenticated collection; unestimable")
        del g["clocks"]
    totals = dict(revisions=len(rows), fixtures=len({r["event_id"] for r in rows}),
                  internal_t30=sum(g["internal_t30"] for g in leagues.values()),
                  internal_t30_fixtures=len({r["event_id"] for r,p in zip(rows, output) if p["internal_t30"]}),
                  external_certified=0)
    assert totals == independent.verify_historical(rows), "independent source audit disagreed"
    splits = split_dates(valid_rows,"date")
    return dict(totals=totals, by_league=dict(sorted(leagues.items())), exclusions=dict(reasons),
                chronological_revision_splits={k:dict(revisions=len(v),fixtures=len({e['event_id'] for e in v}),
                    first=min((e['date'] for e in v),default=None),last=max((e['date'] for e in v),default=None)) for k,v in splits.items()},
                independent_exact_agreement=True), output


def power(events, timed):
    z1,z2 = NormalDist().inv_cdf(.975), NormalDist().inv_cdf(.8)
    delta = .03
    paired_q = {str(q):math.ceil((z1*math.sqrt(q)+z2*math.sqrt(q-delta**2))**2/delta**2) for q in (.2,.5,1.)}
    rows = []
    for league in sorted(set(timed["by_league"]) | {e["league"] for e in events}):
        subset = [e for e in events if e["league"] == league]
        p = sum(e["picks"]["market"] == e["winner"] for e in subset)/len(subset) if subset else None
        if p is not None and p+delta < 1:
            pm = p+delta/2
            independent_arm = math.ceil((z1*math.sqrt(2*pm*(1-pm))+z2*math.sqrt(p*(1-p)+(p+delta)*(1-p-delta)))**2/delta**2)
        else:
            independent_arm = None
        rows.append(dict(league=league, reference_n=len(subset), retrospective_market_accuracy=p,
                         paired_n_q1=paired_q['1.0'], paired_n_scenarios=paired_q,
                         independent_two_arm_n_per_arm=independent_arm,
                         certified_rate_per_week=timed['by_league'].get(league,{}).get('authenticated_fixtures_per_week'),
                         weeks_needed=None, reason="No proven authenticated collection rate; cannot estimate duration"))
    return dict(effect=.03, power=.8, alpha=.05, alternative="two-sided", family_size=1,
                assumptions="paired normal approximation, independent fixtures, prospective discordance unknown; cluster dependence can inflate n",
                rows=rows)


def demo(out):
    """Synthetic witness, intentionally not reusable to certify any real input."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"public synthetic TEST witness only").digest())
    trust = {"SYNTHETIC-NOT-REAL":dict(public_key_hex=private.public_key().public_bytes_raw().hex(),
             valid_from="2026-10-01T00:00:00Z", valid_until="2026-11-01T00:00:00Z", independent=True)}
    p = dict(event_id="SYNTHETIC", product="moneyline", league="TEST", kickoff="2026-10-30T12:00:00Z",
             received_at="2026-10-30T11:00:00Z", source_observed_at="2026-10-30T10:59:59Z",
             source_url="https://example.invalid/synthetic", source_sha256=hashlib.sha256(b"synthetic source").hexdigest(),
             source_kind="raw_response", odds=dict(home=1.8,away=2.0), probabilities={"market":dict(home=.53,away=.47)},
             votes=[dict(author_id="SYNTHETIC-AUTHOR",side="home",source_sha256=hashlib.sha256(b"synthetic vote").hexdigest(),
                         received_at="2026-10-30T10:59:00Z",published_at="2026-10-30T10:58:00Z")])
    path = out/"synthetic-ledger.jsonl"
    # Never overwrite an existing ledger. Each replay uses a fresh output folder.
    if path.exists():
        raise InvalidLedger("choose a fresh output directory for append-only demonstration")
    l = Ledger(path,"SYNTHETIC-ONLY")
    cap = l.append("capture",p,p["received_at"],source_bytes=b"synthetic source")
    fr = l.append("freeze",dict(event_id=p["event_id"],product=p["product"],capture_hash=cap["record_hash"],cutoff_at="2026-10-30T11:30:00Z"),"2026-10-30T11:29:00Z")
    cp = checkpoint_message(l.read(),l.ledger_id,"SYNTHETIC-NOT-REAL","2026-10-30T11:29:30Z")
    cp["signature_b64"] = base64.b64encode(private.sign(canonical(cp))).decode()
    l.append("settlement",dict(event_id=p["event_id"],product=p["product"],freeze_hash=fr["record_hash"],winner="home",final_at="2026-10-30T14:00:00Z",source_sha256=hashlib.sha256(b"synthetic result").hexdigest()),"2026-10-30T14:01:00Z")
    records = l.read()
    assert independent.verify_chain_independent(records,l.ledger_id) == 3
    assert verify_checkpoint(records,l.ledger_id,cp,trust,test_only=True)['authentication_scope'] == 'synthetic_test_only'
    assert authenticated_freezes(records,l.ledger_id,[]) == []
    try:
        authenticated_freezes(records,l.ledger_id,[cp])
        raise AssertionError('synthetic witness entered real certification')
    except InvalidLedger:
        pass
    write_json(out/"synthetic-checkpoint.json", cp)
    write_json(out/"synthetic-trust.json", trust)
    altered = deepcopy(records)
    altered[0]["payload"]["odds"]["home"] = 9.0
    rejected_plain = False
    try:
        verify_chain(altered,l.ledger_id)
    except InvalidLedger:
        rejected_plain=True
    # Owner rewrites every dependent hash/reference consistently.
    references = {}
    for r in altered:
        old = r["record_hash"]
        r["previous_hash"] = references.get(r["previous_hash"])
        for key in ("capture_hash","freeze_hash"):
            if key in r["payload"]:
                r["payload"][key] = references[r["payload"][key]]
        r["record_hash"] = digest({k:v for k,v in r.items() if k != "record_hash"})
        references[old] = r["record_hash"]
    verify_chain(altered,l.ledger_id)  # owner rewrite CAN pass chain alone
    try:
        verify_checkpoint(altered,l.ledger_id,cp,trust,test_only=True)
        raise AssertionError("rewrite authenticated")
    except InvalidLedger:
        pass
    rewritten_cp = deepcopy(cp)
    rewritten_cp["tail_hash"] = altered[cp["size"]-1]["record_hash"]
    try:
        verify_checkpoint(altered,l.ledger_id,rewritten_cp,trust,test_only=True)
        raise AssertionError("forged checkpoint authenticated")
    except InvalidLedger:
        pass
    return dict(synthetic_only=True, records=3, tamper_rejected=rejected_plain,
                whole_file_rewrite_passes_chain_only=True, whole_file_rewrite_rejected_by_pinned_checkpoint=True,
                rewritten_checkpoint_signature_rejected=True, real_authenticated_samples_added=0)


def run(out):
    out.mkdir(parents=True,exist_ok=True)
    # Verify original committed input bytes before any replay.
    for item in json.loads((HERE/'provenance.json').read_text(encoding='utf8')):
        assert hashlib.sha256((HERE/item['destination']).read_bytes()).hexdigest() == item['sha256']
    timed_rows = [json.loads(s) for s in gzip.open(HERE/'inputs/timed.jsonl.gz','rt',encoding='utf8')]
    timed, projections = audit_timed(timed_rows)
    with (out/'historical-projections.jsonl.gz').open('wb') as f:
        with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as z:
            for p in projections:
                z.write(canonical(p)+b'\n')
    raw = json.loads((HERE/'inputs/strict-results.json').read_text(encoding='utf8'))
    strict = [e for e in raw['event_ledger'] if all(e['picks'][k] is not None for k in e['picks'])]
    assert len(strict) == 492
    for e in strict:
        e['evaluation_date'] = stamp(e['kickoff']).date().isoformat() if e['kickoff'] else None
    dated = [e for e in strict if e['evaluation_date']]
    splits = split_dates(dated,'evaluation_date')
    plan = {k:dict(n=len(v),first=min((e['evaluation_date'] for e in v),default=None),last=max((e['evaluation_date'] for e in v),default=None),
                  fixture_ids=[e['fixture_id'] for e in v]) for k,v in splits.items()}
    before = frozen_predictions(dated)
    flipped = deepcopy(dated)
    test_ids = {e['fixture_id'] for e in splits['test']}
    for e in flipped:
        if e['fixture_id'] in test_ids:
            e['winner'] = next(s for s in e['order'] if s != e['winner'])
    assert before == frozen_predictions(flipped)
    test = splits['test']
    metrics = dict(total_strict492={m:describe(strict,m) for m in MODELS},
                   chronology=dict(status='exposed retrospective, not blind', missing_kickoff_excluded=len(strict)-len(dated),
                                   dated_fixtures=len(dated),split=plan),
                   test={m:describe(test,m) for m in MODELS},
                   paired_test_market_control_vs_market=paired(test,'market','market'),
                   paired_test_market_control_vs_fixed91=paired(test,'market','market90_vote10'),
                   heldout_label_flip_invariant=True, predictive_candidates=0, ledger_rules_tried=1,
                   authenticated_performance=dict(n=0,coverage=0,accuracy=None,roi=None,ci=None,reason='no trusted external checkpoint evidence'))
    assert all(x['independent_integer_agreement'] for group in ('total_strict492','test') for x in metrics[group].values())
    result=dict(timed_audit=timed,metrics=metrics,power=power(strict,timed),synthetic_integrity=demo(out),
                conclusion='아니오. 시장 단독 대비 통계적 적중률 개선을 입증하지 못함. 인증 표본 0, 원장 자체는 예측 모형이 아님.')
    write_json(out/'results.json',result)
    write_json(out/'frozen-predictions.json',before)
    manifest = {}
    for directory in (HERE,out):
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or any(k in path.parts for k in ('__pycache__','test-tmp','replay-check')) or path.name in ('manifest.json','validation.json') or path.suffix == '.log':
                continue
            if path.is_relative_to(HERE) and out != HERE and path.is_relative_to(out):
                continue
            manifest[str(path.relative_to(directory)) if directory == HERE else 'OUTPUT/'+str(path.relative_to(out))] = hashlib.sha256(path.read_bytes()).hexdigest()
    repository = HERE.parents[1]
    for relative in ('tests/test_forward_auth_ledger.py',
                     'docs/research/2026-10-30-forward-auth-ledger-protocol.json',
                     'docs/research/2026-10-30-forward-auth-ledger.md'):
        file = repository/relative
        if file.exists():
            manifest['REPO/'+relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    write_json(out/'manifest.json',manifest)
    print(json.dumps(dict(certified=timed['totals']['external_certified'],timed=timed['totals'],
                         strict492=metrics['total_strict492'],test=metrics['test'],
                         paired=metrics['paired_test_market_control_vs_fixed91'],
                         paired_power_q1=result['power']['rows'][0]['paired_n_q1']),ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.output.resolve())
