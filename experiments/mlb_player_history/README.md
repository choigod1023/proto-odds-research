# Bounded MLB player-history acquisition

Standalone standard-library research collector; no production imports or configuration.
See report-20260909.md for the executed results and definition limitations.

Run from repository root (sibling paths refer to the existing corrected PA experiment):

```powershell
python -m unittest discover -s experiments/mlb_player_history -p 'test_*.py' -v
python experiments/mlb_player_history/pilot.py --output experiments/mlb_player_history/private/NEW-RUN --source ../mlb-pitch-pilot-20260909/outputs/pilot-20260909/pilot.sqlite --gate-dir ../mlb-pa-reconcile-20260909/experiments/mlb_pa_gate
```

The corrected gate dependency is commit 50e1d373 (with predecessor fad2f0ec).
The source cache and gate are read only; hashes are recorded. To use after cherry-picking
those earlier changes, point --gate-dir at experiments/mlb_pa_gate instead.
No dependency commit is copied into this delta.

Private output retains raw receipts and normalized per-player JSON in SQLite, coverage.json,
and report.md. Use a fresh output directory for a fresh run. An interrupted acquisition
can reuse its directory: receipt URLs are cached, immutable, and count toward the same
budget. Do not restart in a different directory to evade a stop or budget. Completed
coverage.json/report.md are exclusively created and must not be overwritten.

The live run used 9 requests, including an initial rejected statsByDateRange probe;
a fresh run with the corrected byDateRange type needs only 8 if pagination is unchanged.
No enormous live feeds are fetched. Raw byte budget is 100,000,000, request limit 50,
1.1-second delay, no redirects/retries, durable stop on 403/429 and transport/body errors.
Do not run concurrent processes against the same private database.

The available six-class mapping is deliberately marked unverified: K, BB+HBP, HR,
other hits, and combined otherout/residualreach reconcile to PA or BF, but the last
two categories cannot safely be separated from these aggregate fields. Consequently
this pilot measures history coverage and does not rerun the six-class scoring diagnostic.
Dates are validated using sampled independently dated game logs, not response-echoed
range metadata. Current retrieval of historical stats does not prove point-in-time data.

## Offline five-class follow-up

The separately authorized follow-up safely combines otherout and residualreach into
otherPA. It preserves all corrected rows and compares seeded league vs strength-100
batter/pitcher pooling, with same-class cold baselines. June 1 remains warmup;
June 2–3 use the identical 1,177 PA. Every date updates only after the whole day's
predictions. No network calls or hyperparameter sweeps. See five-class-report-20260909.md.

```powershell
python experiments/mlb_player_history/five_class.py --source ../mlb-pitch-pilot-20260909/outputs/pilot-20260909/pilot.sqlite --history experiments/mlb_player_history/private/run-20260909/history.sqlite --gate-dir ../mlb-pa-reconcile-20260909/experiments/mlb_pa_gate --output experiments/mlb_player_history/private/NEW-FIVE-CLASS-RUN
```

The output directory must be new. It contains results.sqlite with 8,988 prediction
records, summary.json and report.md. Both input SQLite files stay read-only and are
hashed before/after. Source history is reconstructed from validated raw bulk receipts;
full-season game-log validation responses never become model features.
