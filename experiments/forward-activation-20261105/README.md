# Forward collector activation and common-price shadow ledger

## Actual activation

- PR [#576](https://github.com/choigod1023/proto-odds-research/pull/576) merged at 2026-11-04 14:38:50 UTC. Remote main contains `b3f0d5d6044ce9eea501f506d98ac78f22b1d547`.
- The existing Windows task `ProtoKboM0Forward-v1` was enabled, with its original five-minute trigger and settings preserved. Its Python executable is the existing Python 3.14.3 installation. Updating the installed collector does not replace the system interpreter.
- The previous task XML and installation receipt were backed up under the research state directory, `activation-backup-20261104T234121`. No ledger or source archive was removed.
- The first manual run and the following scheduled runs finished with exit code 0. `merged-runtime-activation.json` records the real 2026-11-05 00:03 KST run against the merged runtime. It has zero newly qualified model captures and settlements; this is not an ROI result.
- The additional common-comparison runtime was pinned locally at `4c64a4a0ddd779e8e0ae1af3478c7c15ef66000a`. All 332 archived file hashes and imports, including the standalone operating exporter, were checked before updating the task action. The original trigger, principal and settings were compared and preserved. `common-runtime-activation.json` records its first successful real task run.
- Windows Fly SSH returned a nonzero console-handle error even after producing output. It is not accepted as a successful export. The replacement uses the Fly Machines API through `flyctl machine exec`, checks CLI and remote exit codes, bounds bytes/time, and saves content-addressed exports plus receipts. A read-only diagnostic returned 36 existing operating rows with both exit codes zero. That diagnostic used a test official ID and is not a prospective M0 comparison.

## Comparison path

1. The existing Python observer saves official announcement/history evidence and original Betman prices before T-30. The frozen M0 forecast is appended to its separate model ledger.
2. Only an eligible priced M0 capture can trigger a bounded read-only operating ledger export. Idle cycles do not perform that export.
3. A later pre-T30 operating snapshot supplies one common price frame for M0, operating tipster vote, and market selections. The original M0 forecast and operating ledger remain unchanged. An unavailable, withheld, ambiguous, or late snapshot does not become a successful comparison.
   Exact home/away offer IDs must match the captured Betman market identifier. A degraded fixture/round-only export is retained as diagnostic material and is rejected by this common ledger. The market selection is calculated from inverse common odds, not a possibly different stored probability frame.
4. The first eligible common shadow record is immutable. This is not a reconstruction of the final operating T-30 selection, nor proof that the original M0 capture used the same original prices.
5. Official final scores or cancellations settle all three arms against the common prices. Corrections supersede economic results without creating additional bets. Draws in this two-way product and cancellations refund stakes.
6. No real wager, operating policy, or monthly -50,000 KRW paper wallet is modified.

## Future verification and limits

The directly observed official schedule identified `20261106KTSS0`, KT at Samsung, 2026-11-06 18:30 KST. The prediction cutoff is 18:00 KST. The saved schedule did not announce starters or batting orders. This schedule may change; the automatic collector must verify the current official source again.

Two finite followups were created for November 6, 17:40 and 23:00 KST. They check source evidence, actual appended M0/common records, and postgame settlement. The former periodic AI queue checks and daily AI reports were not restarted. The Python task requires this PC to be awake, logged in, and online.

The real future capture and official final settlement have **not** occurred at activation time. Synthetic tests exercise the lifecycle and failure handling only; they are never appended to the real research ledger and do not supply live accuracy or ROI. Local hashes and SSH receipts are integrity/provenance records, not independent timestamp certification.

`live-verification.json` replays the real local state: the official upcoming opportunity is verified, while announcement, M0 capture, common-price capture and settlement remain unverified. The original M0 price receipt and operating snapshot may be from different times. The new common ledger is an explicitly separate first-common-price shadow cohort; official original operating price bytes and final T-30 operating performance are not claimed verified by this setup.

The full Python suite passed 2,532 tests with 6 skips and 100 subtests before the final transport/verifier integration. The final affected-module suite then passed 172 tests with 1 skip and 5 subtests. `common-runtime-next-scheduled-run.json` verifies the new runtime's following scheduled run at 2026-11-05 00:23 KST, exit 0. No historical ROI experiment was rerun.

New bridge and verification commits are retained in their draft PRs. Their local installation, if recorded in a subsequent runtime receipt, is distinct from merging them into remote main. No Fly application was restarted or deployed for this task.
