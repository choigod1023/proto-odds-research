# KBO retrospective identity resolver

Resolves 239 synthetic 2026 forecast IDs to official KBO game IDs using pinned, local source evidence. It never uses scores or outcomes to identify a game and does not alter operating data.

Run `python -B experiments/expanded-kbo-identities-20261105/resolve.py` from this checkout. External input paths and SHA-256 pins are recorded in `resolve.py`, `resolved.json`, and `audit.json`. The union manifest is the 220-body archive at `C:/Users/user/Documents/Codex/expanded-kbo-markets-20261105-worker/experiments/expanded-kbo-markets-20261105/source-manifest.json` (SHA-256 `2c0aa54de595bf9d44e0f4bba2d0666f1eaf311294c17667313b28a6415d9b5f`). The script verifies every archive body, all 66 official schedule body hashes, and the frozen input files before writing results.

The top-level `resolved` object maps each original `fixture_id` to an official 13-character game ID, date, home and away official team IDs, KST kickoff and source hashes/paths. Match requires unique official schedule teams/date, HEADER_NO=0, matching official game IDs and lineup/starter metadata, and one Betman kickoff equal to the official schedule clock. Resolved official IDs must be unique and not collide with the 470 existing official forecast IDs (whose IDs append the season). `audit.json` records two clock mismatches. Retrospective source and price availability is not proof of pre-event receipt or executable odds. No comparison or settlement is performed.

Run unit tests: `python -B -m unittest discover -s experiments/expanded-kbo-identities-20261105 -p test_resolve.py`.
