# G0 — 24-shard regression modernization

## Scope and source authority

This is one **native Devtool schema-v2 exact-tree overlay**, bound to the verified
PR #29 source commit `b88315d2d47aaecf4adc48c399ff9c86996b05f9` and tree
`fd07cbe94fa717c0124a43028dfcf0a116a94379` on
`Mikeyphw/devtool:feature/g0-native-validation-phase-contract-20261117`.
The previously committed private RUN writer repair is **not** modified or
reapplied. No local Gradle, launcher, clipboard, forced Git updates or merges.

## Current fixes and retained gates

* Use `core_runtime.private_run_root()` for RUN-V3 descriptor, catalog,
  remote relation and Android release evidence assertions. Do not hard-code
  historical `.devtool/private/run-v3` in tests of new XDG authorities.
  Legacy migration and remote-provider compatibility cases stay intact.
* Use `validation_sessions.session_path()` for schema 4/5 migration fixtures.
  Preserve revision checks and negative/blocked session controls. The control
  API test must not create its session in one XDG root and issue requests in
  another.
* Make explicit `--validate` honor `validation.restore=false`,
  `validation.build=false`, and `validation.test=false` rather than requesting
  the default Android/Gradle validation pipeline. A **separate** existing test
  still asserts rollback after a genuinely failing validation command.
* Resolve the rollback journal with `transactions.transaction_root()` rather
  than a guessed `xdg-data` pathname. Preserve interrupted-rollback,
  checkpoint, index-drift, user-edit and identity-conflict negative tests.
* Replace an independent TOML parse in affected-ownership diagnostics with
  `load_wrapper_project_config()`, the canonical schema-validated loader.
  Retain ignored-metadata and root-wildcard warnings, canonical ownership
  precedence and rejection of out-of-repository configuration files.
* Classify seven previously unreviewed destructive-operation modules by
  **exact expression and occurrence count**, with non-waiver rationales.
  Keep the fail-closed scanner and high-risk disallow-list. Do not replace
  them with a broad file exclusion or delete security coverage.
* Keep the earlier private-writer test, storage migration tests, post-apply
  artifact cleanup tests, identity/authority guards and full CLI tests.

## Validation and acceptance

An immutable checksummed Python-only native validation script runs the changed
XDG/validation/LIFE/ownership tests together with negative storage and cleanup
regressions, using the repository's own pytest runtime. Its own result is an
actual native Devtool validation receipt only **after** `validate-overlay`,
`overlay check` and native `apply-overlay --validate` succeed on the exact HEAD.

The historical failed run `44175763793` had 117 failures on 21 of 24 shards.
Those failures are **not** automatically considered fixed by a static overlay
check. PR checks must be checked at the resulting commit, followed by a **new**
source-bound `devtool actions run devtool-full-repository-pytest.yml` against
that branch. Require 24 successful shards **and** the fail-closed summary.
Report any remaining failures by actual job and test, revise the consolidated
artifact without bypassing failing coverage, and do not merge automatically.

## Safety boundaries

* No changes to the first private-RUN writer repair.
* No automatic clipboard access; retain native Rich diagnostics.
* No broad chmod of existing user-owned directories.
* No rebase, hard reset, force push, or merge.
* Never claim skipped, unexecuted, or synthetic checks passed.
* A mutated source tree or payload hash mismatch refuses before apply.

## Consolidated R2 validation corrections

* The ownership diagnosis compares the canonical target-root path with the
  repository root. `TargetConfig.root` is an absolute path after the canonical
  loader; comparing it to the pre-normalized `"."` suppressed a valid warning.
  Keep the ignored-metadata warning and add a nested-root false-positive test.
* The canonical storage inventory labels historical `.devtool/logs` as
  `legacy-log`, not the deprecated generic label `legacy`. The test still
  requires `pass` for classified historical data and `fail` for unknown state.
* The prior native run reported 206 passed, 2 failed, zero skipped; R2
  corrects those two issues without deleting tests or weakening gates.
