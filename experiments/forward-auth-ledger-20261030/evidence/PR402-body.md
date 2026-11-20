Compare five fixed selection rules on the same archived fixtures in the requested 2025-10-29–2026-10-28 window: market reference, market/vote 90:10, 75:25, 50:50, and tipster plurality. Preserve compact source facts, hashes, fixture-level picks, independent settlement checks, coverage/abstention counts, weekly intervals, league/time splits, and a Korean report.

Strict common cohort: 492 games. Hit rates are 57.52%, 57.52%, 53.25%, 54.27%, and 54.27%; every flat-stake ROI is negative. Supplemental overtime-assumed cohort: 580 games; 90:10 reaches 58.97% versus market 57.76%, but the paired improvement interval includes zero.

Limitations: reuse of PR #398's previously seen retrospective archive, incomplete season coverage, no independently certified pregame versions/purchase-time prices, and only 17/21 common fixtures with multiple voters. This does not replay the complete historic production model, select a winner for deployment, or prove future profit. Supplemental and strict cohorts overlap and must not be added together.

Validation:
- Full Python suite: 1,581 passed, 93 subtests passed, 5 skipped.
- New regression suite: 19 passed, including independent corpus settlement.
- Every saved pick/return of the three legacy arms agrees fixture-by-fixture with the original PR #398 archive.
- Each model's shared-across-sports paper account reserves same-day stakes and respects the KRW 50,000 monthly loss limit.
- LF-pinned evidence passes exact input hash checks after Git normalization.

No production code, operational policy, DB, infrastructure, merge, or deployment changes.
