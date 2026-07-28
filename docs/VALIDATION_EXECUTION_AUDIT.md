# Validation and execution-environment audit

This audit covers resumable validation planning, checkpoint migration, deferred
failure recovery, Termux/chroot delegation, environment/path sanitation, and
automatic project-kind selection.

## Validation invariants

- Repository validation checks repository behavior only. Overlay/archive
  integrity is checked by artifact inspect/apply before extraction.
- Android resumable validation uses stable phases:
  `preflight`, `dependencies`, `compile`, `resources`, `unit-test`, `package`,
  `lint`, optional `instrumentation`, and `finalize`.
- Phase identity is based on scope, phase name, and occurrence rather than the
  old list index. Adding a tail phase does not replay completed work.
- A changed phase command invalidates that phase and downstream phases only.
- Removed legacy `artifact-check` records are discarded during session
  synchronization without replaying a completed package phase.
- Interrupted phases return to `pending`. A genuine deferred validation failure
  blocks the session, preserves the applied tree and rollback point, and leaves
  dependent phases pending unless an explicit rollback policy is selected.
- Schema-v4 machine controls use optimistic session revisions. Read-only status,
  plan, and event polling cannot reclaim a live phase, while accepted pause/stop
  requests advance the durable revision without heartbeat churn.
- A stale apply rollback is a recovery boundary for non-apply commands. The
  requested command is not executed until the user reviews the restored tree
  and runs it again.

## Chroot eligibility invariants

- Automatic or explicit chroot delegation is supported only from native
  Termux, or when Devtool is already running inside the selected chroot.
- A desktop Linux host never auto-selects the Android/Termux chroot merely
  because `chrootctl` exists or a sparse global preference allows that project
  kind.
- Desktop requests for `execution_environment = "chroot"` fail preflight with
  an actionable message instead of probing or launching `chrootctl`.
- Native Termux paths are translated before guest execution. Remaining
  `/data/data/com.termux/...` values in cwd, argv, or environment are rejected.
- Host-only `PREFIX`, `JAVA_HOME`, Android SDK, Gradle, venv, Python home, and
  temporary-directory values are not exported into Ubuntu when they point to
  Termux.
- A Termux Python path is resolved as guest `python3`; the host minor version is
  not assumed to exist inside Ubuntu.

## Project-kind selection invariants

- Detection is performed from the repository path passed with `-r`, not from a
  parent directory that happens to contain `.devtool.toml`.
- Exactly one repository-root marker wins over unrelated nested examples,
  demos, mobile apps, or tools.
- Multiple root-local kinds, or multiple nested kinds with no root-local owner,
  are ambiguous and require `[project].kind`, `--project-type`, or a typed
  `--target`.
- A typed target remains authoritative in a mixed workspace.

## Regression coverage

Focused coverage exercises desktop hosts with a fake `chrootctl`, native
Termux delegation, direct execution inside chroot, host-path leakage through
argv and environment variables, Python bootstrap selection, root-versus-nested
project markers, stable session keys, plan insertions/removals, command
fingerprint changes, stale rollback boundaries, and removal of repository
artifact checks.

## Phase 32–33 report and release audit

Validation sessions write schema-1 reports under `.devtool/reports/validation/`. Finalization rechecks required phase states, minimum checkpoints, and repository input fingerprints. The release scenario matrix is machine-readable at `docs/validation-release-scenarios.json` and is enforced by `tools/validate-validation-release.py`.
