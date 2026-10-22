# Bounded MLB PA smoke experiment

Standalone Python standard-library experiment. No production imports, networking,
collection, model selection, or game-win predictions. Read the aggregate report for
the restrictive five-category population and acquisition limitations.

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
K, BB-HBP, HR, otherhit, otherout. Brier uses the sum of five squared errors.

Tests use synthetic feeds and cover event guards, unknown labels, duplicate
identity, date isolation, future invariance, exact smoothing, unseen players,
metric definitions, input integrity, read-only access, and a private end-to-end run.
