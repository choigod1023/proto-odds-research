# Year-matched tipster/market/fixed91 offline dataset

- Frames / physical events: 597. Strict: 508; additional NBA standard-OT: 89.
- Dates (KST): 2025-10-29–2026-10-27; 156 observed dates.
- Choices: 1257; unique event/direction pairs: 1224.
- Actual known-round choices: 1257; unknown-round choices: 0.
- Frames with one round: 581; frames with multiple saved issue rounds: 16.
- Zero-vote choices: 626; all retain zero score and are ineligible for pure vote selection.
- Every frame is retrospective and nonexecutable. available_at is hypothetical kickoff minus 24h; availability_proven=false; quote_observed_at=null.
- Saved official aware kickoff is used where available; otherwise saved aware Proto archive kickoff; canonical noon would be explicitly marked if needed.
- market is Shin(actual archived odds); market90_vote10 is round(0.9*market+0.1*actual author vote share,4), matching saved choices. Both are uncalibrated estimates.
- vote_support is actual source/author support, never calibrated p/EV. probability_models excludes it. No ROI was calculated.
- Offer IDs are derived coordinate keys, explicitly not independently authenticated operator IDs. Raw offer records, capture times, publication claims and cutoff flags are preserved.
- Standard-OT increment assumes overtime inclusion without original-bookmaker verification and is a distinct cohort.

## Coverage

- KBL: 18 events; 2026-03-05–2026-10-25; 14 actual year/round pairs.
- KBO: 217 events; 2026-03-29–2026-09-08; 35 actual year/round pairs.
- K리그1: 19 events; 2026-03-07–2026-09-20; 8 actual year/round pairs.
- K리그2: 11 events; 2026-03-07–2026-09-20; 6 actual year/round pairs.
- MLB: 116 events; 2025-11-01–2026-10-24; 40 actual year/round pairs.
- NBA: 99 events; 2025-10-29–2026-10-27; 49 actual year/round pairs.
- NPB: 117 events; 2026-03-31–2026-09-16; 41 actual year/round pairs.

## Exact sources

- [evaluate_tipster_expansion.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/scripts/evaluate_tipster_expansion.py) — SHA256 `c14346315e410497150c4f3d87dfe5c17563e69e80c661f05e7068d150a37187`
- [join_tipster_expansion.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/scripts/join_tipster_expansion.py) — SHA256 `f38e6f3316a925f65b6cf6992f69ed922fa1155d41b4d284f802ddf09667ca5b`
- [collect_tipster_year_nba.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/scripts/collect_tipster_year_nba.py) — SHA256 `db253a2488b31e3bbb05afa7b4a501fadb4afc912ac3fc59d9eaec3c32a6bef5`
- [tipster_year_aliases.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/scripts/tipster_year_aliases.py) — SHA256 `c59f486ccb1fbff947510ebc401d7711c0e215062a800ac73b63ceb6bd500a53`
- [devig.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/src/devig.py) — SHA256 `2a102be60e3e07c03644afff1480f72e07d2fe1d08165f98cfed5bc5b29f31c9`
- [compare_year_model_accuracy.py](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/scripts/compare_year_model_accuracy.py) — SHA256 `00e6cdbf8442aac5e991bb00260b1abfd354e97aaef3051eedac3a8b59399c86`
- [strict.jsonl](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/experiments/year-model-hit-20261029/strict.jsonl) — SHA256 `702c4f830218b695c12d79acf42c30ce624f40237137a4c3d7f549a4d887b650`
- [strict-results.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/experiments/year-model-hit-20261029/strict-results.json) — SHA256 `5d433838cf654ff6227bc617efbe43907a572850f6a883f6969967f414b23e76`
- [results.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/results.json) — SHA256 `9723ccb17e59727b501622b2782894cde89e21fdbc4feeeef2f559d7d65bbd3c`
- [standard-ot.jsonl](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/experiments/year-model-hit-20261029/standard-ot.jsonl) — SHA256 `6a4ba9853dbb60d925b4ca9a7c27897e92ddb9a0dc595ca27a11d123c30f029c`
- [standard-ot-results.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/year-model-hit-comparison-20261029/experiments/year-model-hit-20261029/standard-ot-results.json) — SHA256 `c275db598200b3a9cb2ef774bc8667bf2692ed2874aaac75e6ffac06067c47b4`
- [results-standard-ot.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/results-standard-ot.json) — SHA256 `05b346609fba6250481117c916bb317452b04ce4a4dc6c5f2eac6481ddd1f6e4`
- [offers.jsonl](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/prices/offers.jsonl) — SHA256 `54c626a9160c7c7547af6ac1c3e8d6776797eed1aa9929c9d332bd2200cbdb44`
- [MLB-sources-and-models.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030/experiments/league-loss-cap-20261030/source-audits/MLB-sources-and-models.json) — SHA256 `22926d99944ae11a6da2386228c705be7d16b8e1ace4bb87284b50d2d5864992`
- [NPB-manifest.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030/experiments/league-loss-cap-20261030/source-audits/NPB-manifest.json) — SHA256 `d9ce8751f4cff9488f6afc019430f3a19cc920de8b67c628baa55eefac47288e`
- [KBO-normalization-checks.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030/experiments/league-loss-cap-20261030/source-audits/KBO-normalization-checks.json) — SHA256 `211aa98fad34d8452441c79c3882a4da62623a1101dbb58c594654cd8ed1fa1f`
- [cohorts.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030/.local-test/KBO/cohorts.json) — SHA256 `fdb141ffbbe10692f867d6f8cd16e8ac85ec81654a374de12959723721f3129f`
- [events.json](C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/league-loss-cap-ablation-20261030/.local-test/NPB/events.json) — SHA256 `15f710da49cf742d2a7ec3e3528f6ad1bcf774b7688a8a63c67c7786a197ef83`

## Validation and limits

- Python rebuilt the 508/597 saved event ledgers exactly from actual raw forecasts; deduplicated by league/date/canonical teams; checked source/author counts, saved model directions, prices, official results, positive-support eligibility, source hashes and 24h proxy offset.
- Older loss-cap audit contributes no rows here: KBO198 and MLB116 overlap; five additional NPB expansion events were identified but excluded under the parent’s latest year-only scope.
- Results rely on existing official-result joins, not a new independent operator audit. Raw archive scores are checked against those results after matching; outcomes never determine identity, ranking or vote eligibility.
- No source-proven publication/quote availability, execution, real settlement time or fresh unseen holdout is established.
- Full coverage, exact issue-round lists, source hashes and checks are in manifest.json and validation.json.
