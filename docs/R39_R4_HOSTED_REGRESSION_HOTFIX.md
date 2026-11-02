# R39-R4 hosted regression hotfix (non-release)

Applies only to Devtool source after R39-R4 consolidated source-gate acceptance.

## R34 generated DAG source parity

The generated hosted Android workflow was migrated to Node24-compatible immutable GitHub Actions SHAs, but the source-controlled R34 generator retained its older pinned SHA constants. This hotfix aligns those constants with the already-migrated workflow; no task graph, ordering, signing, Gradle workload, or execution authority changes.

The exact full-text generator-to-workflow equality test must keep passing. If generated YAML changes beyond the six reviewed Action references, fail closed rather than regenerating and overwriting unrelated work.

## G00 historical immutable source snapshot

G00/G01 evidence references the 2026-10-13 source capture at commit `e72335640aab0db58ae96c3e319a6bcec1e2ac19`. Its three historical workflow SHA-256 hashes remain unmodified in `docs/actions/ACTIONS_G00_G01_EVIDENCE_INDEX.json`.

The **original captured bytes** are preserved under `docs/actions/frozen_source/e7233564/.github/workflows/` and are independently verified against those hashes. Live workflow files can receive maintenance and security upgrades without pretending to be byte-identical to historical captured workflows. No historical test result or qualification is upgraded by this change.

## Acceptance

Run `tests/test_r34_generated_android_lab.py`, `tests/test_actions_g00_evidence_frozen.py`, and `tests/test_r39_r4_hosted_regression_hotfix.py` plus relevant G02–G08 and R39 regression tests locally. Full 24-shard hosted regression for the *new exact commit* is mandatory. No real Android device, Gradle, signing, real-consumer, or formal release qualification follows from this source-only fix.
