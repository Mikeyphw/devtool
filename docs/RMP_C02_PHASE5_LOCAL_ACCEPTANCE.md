# RMP-C02 Phase 5 — Local caller-path acceptance

This additive candidate verifies **source-controlled, disposable test-owned caller and recovery paths** with a machine-readable JSON receipt. It is not release qualification and does not close RMP-C02 by itself.

Run `python3 -m devtool.rmp_c02_acceptance --repo .` from the committed security branch. To save evidence without overwriting previous receipts, create an output directory and pass `--output /path/to/new-unique.json`.

It executes a fixed, bounded subset of C02 tests covering independent worker invocation denial, source authority, symlinked parent retargeting, normal apply cleanup, duplicate-download behavior, provenance, worker abnormal termination and receipt reconciliation. It pins file digests of the relevant source and every included test. It reports a failure for a nonzero/timeout/incomplete test run and **never claims formal closure**.

The runner strips inherited Devtool transaction/run environment and replaces PYTHONPATH with the source repository, so tests cannot accidentally attach to the overlay install transaction. No network access, actual user downloads, remote worker, CI24, G3R, RS13, or Android device qualification is touched.

## Phase 5 manifest contract

The overlay must declare native automatic commit intent in `.devtool-artifact.json` as `apply.commit: {enabled: true, strategy: single, message: ...}`. Validation happens before Git commit. The installer must not add `--no-commit` or `--commit-mode none` and must check post-apply Git HEAD and parent identity after successful completion. A Git commit failure must be reported, not silently interpreted as a successful implementation.

## Remaining obligations

A passing local receipt does not qualify native Termux behavior, independent malicious same-user actors, or all external caller paths. Inspect the on-device JSON and resulting source commit before revising the original RMP-C02 risk family status. All original 308 historical entries and six audit obligations stay preserved.
