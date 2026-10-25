# Bug reports include implementation

- For reports of missing or incorrect project behavior, investigate and implement the confirmed fix in the same task; do not stop at a cause report or ask again whether to change code.
- Read `docs/PRODUCTION_CONTRACT.md` before changing production behavior. Preserve frozen picks, DB records, live/result state, and concurrent worktrees.
- Use a dedicated `codex/` branch/worktree from latest remote main. Add regression tests, run relevant tests/build/browser checks, then commit, push, and open a PR.
- Respect explicit explanation-only/no-change requests. If no defect is established, report evidence rather than inventing changes.
- Merge, deployment, production restarts, paid scaling, and destructive actions still need their applicable user authorization. Report those states separately from implementation.

# Research scope and evidence

- Before continuing prediction research, read `docs/research/2026-10-25-research-scope-correction.md`.
- Distinguish the requested comparison from the experiment actually executed. Never substitute an odds/form-only experiment for a requested tipster/model/hybrid comparison and report that request complete.
- Every result must identify the actual input sources, model definitions, matched event counts, time splits, and execution artifact. Clearly state omitted inputs and unverified claims.
- A research PR merge preserves code and evidence; it does not mean model adoption, production deployment, or proof of future profit.
