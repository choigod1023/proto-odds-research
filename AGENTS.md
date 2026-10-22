# Bug reports include implementation

- For reports of missing or incorrect project behavior, investigate and implement the confirmed fix in the same task; do not stop at a cause report or ask again whether to change code.
- Read `docs/PRODUCTION_CONTRACT.md` before changing production behavior. Preserve frozen picks, DB records, live/result state, and concurrent worktrees.
- Use a dedicated `codex/` branch/worktree from latest remote main. Add regression tests, run relevant tests/build/browser checks, then commit, push, and open a PR.
- Respect explicit explanation-only/no-change requests. If no defect is established, report evidence rather than inventing changes.
- Merge, deployment, production restarts, paid scaling, and destructive actions still need their applicable user authorization. Report those states separately from implementation.

# Prediction research: final objective

- The user's final objective is higher **actual recommended-pick hit rate**, not merely better auxiliary metrics. Keep this objective in every prediction experiment and handoff.
- Brier/log loss, xG fit, plate-appearance accuracy, data coverage, and collection volume are intermediate diagnostics; never present their improvement as proof that game or recommendation hit rate rose.
- Evaluate the exact pregame pick and settlement rules, then apply the existing recommendation eligibility/selection policy. Report hits, misses, pushes/voids/unsettled separately, settled denominator, recommendation count and coverage, date range, and league/sport breakdown.
- Compare against the current production baseline on the same available games/time window. If only a market or research baseline can be reconstructed, label it explicitly; do not call it production performance.
- Compare both unchanged-policy output and matched recommendation counts. Disclose removed/added picks and odds mix; fewer picks or lower odds alone are not evidence of better predictive ability. Treat return as a separate guardrail, not a replacement objective.
- Train/select using past data only, freeze candidates before testing, and reserve an untouched chronological holdout. Repeated experiments on the same test period are exploratory, not independent confirmation. Never use postgame features or revised picks as pregame predictions.
- Persist hypotheses, fixed settings, data/code provenance, all outcomes including failures, limitations, and next decisions in private SQLite and Markdown reports. No React/Canvas report or scheduler by default.
- When asked to continue research, execute bounded in-session hypothesis -> experiment -> hit-rate evaluation -> feedback iterations; do not stop at plans or silently create cron jobs. Do not promise guaranteed improvement or autonomous work after the turn ends.
- Adopt production changes only after recommendation-level improvement survives longer league-specific validation and the applicable authorization; otherwise record the failed hypothesis and investigate the next justified alternative.
