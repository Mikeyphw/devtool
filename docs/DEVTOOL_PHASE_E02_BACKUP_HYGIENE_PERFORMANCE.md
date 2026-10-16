# Devtool Phase E02 — FS03 backup-hygiene performance and qualification evidence

## Baseline, impact, and boundaries

- Source preimage: `b2bd5f979c54e7287ec6c1f606f7996b275c5639` (Phase E01 qualified; GitHub 24-shard run `38875056948`).
- Direct native observation: Devtool `qualify-seal` run `68155a42bca64a30a6d6312dd78342c3` on the **older** source `1bb1053f29ba33bf5283244b2badf3f3dccf4581` returned `PASS_AUTOMATED_MILESTONE_ONLY` after approximately 1,011 seconds of inner work. CI24 took 7.236s and native G3R 0.862s. FS03 took 1,002.461s, of which `repository.backup-hygiene` took **998.111s**. Do not attribute that time to GitHub or G3R.
- New source status: local synthetic, regression, and package validation only; no native Termux performance measurement or current-source remote qualification implied by this change.

## Diagnosis

`final_seal._workspace_backup_paths()` called `discover_backup_candidates()`, which recursively traversed every non-`.git` and non-`.devtool` directory and examined every backup-shaped *file*, then constructed a LIFE-X03 ownership view for all candidates. Filename heuristics (e.g. `*.bak`, `*.orig`) use `managed=False`; LIFE-X03 assigns them `PROTECTED_UNKNOWN`, so none may be selected as an automatically cleanable candidate. The FS03 contract is to reject **eligible** backup candidates, not enumerate every protected heuristic file in caches and enormous ignored dependency trees.

## Change

- `discover_backup_candidates(root)` remains the original broad discovery and full ownership-plan API for explicit cleanup operations.
- `discover_backup_candidates(root, managed_only=True)` is used **only** by FS03. Its native `find` traversal lists candidate directories with Devtool's explicit producer-owned backup prefixes, excluding `.git` and `.devtool`, without Python stat calls for every regular file. If no managed directories are found, the selected set is necessarily empty and FS03 avoids opening irrelevant LIFE/RUN history registries. Otherwise it invokes the unchanged LIFE-X03 descriptor, ownership view, references, and cleanup plan before returning *selected* paths. Nested managed directories are supported; descendants of a selected managed backup are pruned just as the old scan did.
- On a host without `find`, a Python `os.walk` fallback preserves the same directory selection. A failed/interrupted native scan, Python walk error, or 120-second timeout blocks FS03; none yields a successful empty scan.
- No cleanup, deletion, bypassing, Git dispatch, or release promotion occurs during this read-only check.

## Equivalence and testing

The optimization relies on an executable invariant in `cleanup_lifecycle._safety_for`: `not descriptor.managed` yields `PROTECTED_UNKNOWN`, which `compile_cleanup_plan` rejects. Generic filename and unknown directory heuristics are never authority to clean up files. The Phase E02 test compares broad and optimized eligible sets, including nested managed backup directories, sibling and nested heuristics, excluded repository state, an absent `find` fallback, and failed or timed-out native scans. It also verifies that FS03 still returns any eligible backup as a failure.

Local synthetic 12,000-irrelevant-file benchmark (Linux, not Android): broad scanner 0.386s vs. `managed_only` 0.031s; both returned the same managed backup path. This is not a prediction of the Termux improvement. Full regression should run on the installed commit before any new release seal.

## Remaining work

- Requalify the resulting commit once with CI24, native G3R 13/13, and strict FS03 16/16; retain run receipts and source SHA.
- Measure `fs03.repository.backup-hygiene` and `stage_durations_seconds` on Termux. A scan exceeding 120 seconds must be diagnosed as incomplete, never cached as PASS.
- The six release-substrate obligations remain open: `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11`.

This document is the cumulative improvement ledger's **Phase E02 addendum** (`QW-PERF-005` and `PHASE-E-PERF-009`); older findings and evidence remain retained in `docs/DEVTOOL_IMPROVEMENTS.md`.
