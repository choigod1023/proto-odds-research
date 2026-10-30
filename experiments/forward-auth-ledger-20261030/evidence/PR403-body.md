Investigate which model components and inputs are worth testing to improve hit rate beyond fixed market/tipster blends. The Korean roadmap combines primary research with the current main selection path and explicitly distinguishes previously failed features from new candidates.

Findings:
- Current author-plurality selection leaves recognized lineup/absence evidence as context only. Three synthetic context variants keep the same pick/probability; this is a mechanism probe, not predictive validation.
- Re-scoring PR #402's existing cohort: on 180 market/vote disagreements, market is correct 98 times versus vote 82. Only 17/492 strict common fixtures have multiple voters.
- Positive temperature scaling changes zero choices in 60,000 synthetic comparisons; it cannot itself improve argmax hit rate.
- Prior xFIP, lineup/wOBA, and form/schedule failures are retained as counterevidence. Literature supports investigating player contributions, expected minutes/innings, independent crowd information, and market-conditioned regularized integration; no new accuracy or ROI gain is claimed.

Validation: full Python suite 1,565 passed, 93 subtests passed, 5 skipped; 3 new focused tests passed. Saved input provenance includes fixed upstream commits and LF-normalized hashes.

Research only. No new predictor trained, no new collection adapter, no production policy/DB/infra/deployment changes. The KRW 50,000 monthly paper loss limit remains part of the proposed validation contract.
