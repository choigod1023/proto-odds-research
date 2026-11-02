# Bounded MLB PA smoke experiment

Standalone Python standard-library experiment. No production imports, networking,
collection, model selection, or game-win predictions. Read the aggregate report for
the six-category population and acquisition limitations. The tracked report dated
20260909 describes the earlier five-class run; use the newly generated private
report for reconciliation results.

From the repository root:

```powershell
python -m unittest discover -s experiments/mlb_pa_gate -p 'test_*.py' -v
python experiments/mlb_pa_gate/gate.py --source ../mlb-pitch-pilot-20260909/outputs/pilot-20260909/pilot.sqlite --output experiments/mlb_pa_gate/private/run-20260909
```

The output directory must not already exist. It contains private `results.sqlite`
and an aggregate `report.md`. `private/` is ignored. The source is opened with
SQLite `mode=ro` plus `query_only`, each referenced raw body hash is validated,
and the input file hash is compared before/after extraction and evaluation.
No source rows are exported into tracked files. `report-20260909.md` is the
sanitized aggregate export from the bounded run.

Parameters: symmetric league pseudocount 1 per category, player prior strength
100 per batter/pitcher, equal pooling. The first official date warms history;
all games on each subsequent date are scored before any same-date update.
Only those later dates enter aggregate scores. Probability order is
K, BB-HBP, HR, otherhit, otherout, residualreach. Brier sums six squared errors.
Residualreach covers field_error, catcher_interf and fielders_choice; unseen
event types still fail closed into audited exclusions. This covers every observed
PA event in the bounded cache, not a promise to accept every future API event.

Actual cached team boxscore plateAppearances are compared separately for home and
away. Batter PA differences and full affected play records are stored privately;
missing counts/half-inning attribution stay explicit and never trigger imputation.
Input and both implementation-file hashes are stored in SQLite and Markdown.
Evaluation scores include distinct game counts and sparse-history diagnostics.

Tests use synthetic feeds and cover event guards, unknown labels, duplicate
identity, date isolation, future invariance, exact smoothing, unseen players,
metric definitions, input integrity, read-only access, and a private end-to-end run.
