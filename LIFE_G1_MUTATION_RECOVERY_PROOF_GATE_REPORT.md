# LIFE-G1 — Mutation / Recovery Authoritative Proof Gate

Date: 2026-09-29
Campaign position: gate-window 3 of 3; full-plan 33/70
Window audited: LIFE-X01 (31/70) + LIFE-X02 (32/70)
Next major scope: Gradle Takeover, beginning ENV-X05 (34/70)

## Gate decision

LIFE-G1 is a separate authoritative gate. The audit re-read the LIFE-X01/LIFE-X02 ledgers, exported obligations, the 2026-09-28 transaction/recovery incident promises, and the live post-X02 implementation. Two real lifecycle gaps were found and folded into this gate before sealing the window.

## Gate-owned closure 1 — atomic bundle rollback authority

`create_overlay_bundle()` previously emitted the contradictory combination `rollback_on_failure=true` with `failure_action=pause`. The bundle validator regression could therefore retain bundle payload after validator failure depending on which projection was consumed. New bundles now emit one unambiguous destructive mutation policy: validator failure rolls back the atomic bundle (`failure_action=rollback`). Evidence/diagnostics may survive independently, but repository mutation does not.

The authoritative regression `ArtifactV2Tests.test_bundle_validator_failure_rolls_back_every_overlay` proves both nested overlays disappear after bundle validator failure.

## Gate-owned closure 2 — validated result publication recovery

LIFE-P125 required a detached, already-validated isolated result commit to become a durable resumable publication phase rather than being discarded on the next invocation. The gate adds:

- durable detection of a publication-ready transaction only when status is exactly `validated-uncommitted`;
- exact result-commit verification in the private transaction worktree;
- startup reservation ownership for that one recoverable transaction without weakening ordinary pending-validation exclusion;
- idempotent publication through the existing isolated-worktree integrator;
- primary HEAD/index/worktree baseline re-verification immediately before mutation;
- fail-closed behavior when the user changed the primary checkout; and
- no automatic publication merely because an unvalidated worktree commit exists.

## Additional gate findings closed

- Repository-pack relative-path normalization no longer strips a leading dot from names such as `.devtool.toml`.
- Repository-pack failed verification preserves the last published pack and removes staging files.
- Inline artifact validation declarations fail closed when they name an unsupported runner instead of reaching runner dispatch ambiguously.
- Missing rollback metadata returns typed `rollback_unavailable` evidence and is never projected as verified recovery.
- Later Devtool-owned transaction generations remain rollback-protected and restore the original repository generation.
- Same-run artifact validation refs resolve against the post-apply candidate configuration.

## LIFE-P123..P127 gate proof

- **P123 — recovery Git-state normalization:** retained X01 convergence/rollback regressions prove restored content does not become phantom dirt solely from host/stat-mode differences.
- **P124 — content-verified preserve-dirty conflicts:** byte identity + Git executable semantics permit true convergence; genuine content/executable/untracked collisions remain fail-closed.
- **P125 — idempotent result publication:** validated-uncommitted result commits resume publication; unvalidated commits do not; primary drift blocks publication.
- **P126 — user-dirt provenance:** fresh-process and retained dirty-tree tests preserve genuine user changes and reject destructive overlap.
- **P127 — post-apply validation registry truth:** a same-run first-class test ref resolves from the post-apply candidate config.


## Qualified-host gate attempt v1 — hermetic fresh-process fix

The first qualified-host LIFE-G1 attempt (`8740ef6a4de940cc85fdec5e340838a2`) rolled back cleanly after one gate-test failure. The product-side publication-drift assertion did not fail; the fresh subprocess was intercepted earlier by inherited user/global execution configuration (`host-android`), which is not a current canonical execution-environment token.

Gate v2 makes the fresh-process fixture hermetic without weakening production behavior: it preserves real host markers such as Termux `PREFIX`/`HOME`, but clears parent-run routing identity (`DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT`, execution-context/run/active-target state) and points global Devtool config at a temporary empty config home. The same publication-drift test then reaches the intended recovery path and proves the primary checkout remains untouched on drift.

The failed v1 apply reported `rollback_state: verified` / `rollback_verified: true`; no v1 gate mutation survived.

## Validation evidence

Local audit-loop proof on the post-X02 source state:

- `tests/test_life_g1_mutation_recovery_proof_gate.py`: 14 passed.
- LIFE gate + retained X01/X02 + transactions/artifact/bundle suites with `ResourceWarning` promoted to error: **161 passed, 0 failed**.
- Artifact-ref/validation-convergence regressions: **16 passed, 0 failed**.
- `python -m compileall -q devtool tests/test_life_g1_mutation_recovery_proof_gate.py`: PASS.
- Existing atomic bundle validator rollback regression: PASS.
- Full canonical `roadmap-life-g1` equivalent retained boundary: **119 passed, 0 failed**, with ResourceWarning promoted to error.

The canonical `roadmap-life-g1` workflow also retains source syntax, X01 immediate safety, X01 transaction recovery, X02 canonical model, and atomic bundle rollback proof. The artifact requires zero warnings.

## Window ledger

- LIFE-X01 qualified host run: `0a5a5bd48a574c1b91b168918a9c919f`, commit `c14bfd5`, zero warnings/errors.
- LIFE-X02 qualified host run: `203140a095db4d2bbaed3d3f2fad7b3d`, commit `5a4dfca`, zero warnings/errors.
- LIFE-G1: pending qualified-host apply of gate artifact v5.

No implementation work is carried past this gate. After qualified-host LIFE-G1 succeeds, M006 LIFE is closed and the next position is ENV-X05 / Gradle Takeover at 34/70.

## Qualified-host gate attempt v2 — core execution-environment leak

The second qualified-host attempt (`841eea0c889d40bc83e8251c9364167d`) also rolled back cleanly and verified. The v2 fresh-process scrub removed `DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT`, but Devtool core runtime has a separate higher-priority routing input, `DEVTOOL_EXECUTION_ENVIRONMENT`. The Termux parent session exported the legacy value `host-android`, so the nested fixture still failed before transaction publication recovery was reached.

Gate v3 now clears **both** execution-environment routing variables for fresh-process fixtures while preserving real host markers. The exact failing host condition was reproduced locally by launching the complete canonical gate boundary with `DEVTOOL_EXECUTION_ENVIRONMENT=host-android`; all **119 tests passed**, with `ResourceWarning` promoted to error. Production execution-environment normalization remains strict and unchanged.

The failed v2 apply again reported verified transaction rollback; no v2 gate mutation survived.


## Qualified-host gate attempt v3 — auto host-detection cross-scope leak

The third qualified-host attempt (`95a97b89d9274fd3939611077d3d753c`) again rolled back cleanly and verified. Clearing both execution-environment variables was still insufficient because the nested recovery probe left execution selection on `auto`. Under the validation runner's sanitized process environment, Devtool could derive the diagnostic host substrate `host-android`; that diagnostic substrate is intentionally not a canonical executable environment token and therefore failed normalization before publication recovery executed.

This was a gate-harness ownership error: LIFE-G1 owns mutation/publication recovery, while automatic host/execution-environment classification belongs to ENV. Gate v4 therefore binds nested Devtool recovery probes explicitly to the parent process' canonical execution substrate. On Termux the binding is `native-termux`, recognized from TERMUX markers or the Termux Python executable path; desktop/chroot/SSH/PRoot substrates use their canonical tokens. Non-canonical diagnostic `host-*` values fall back to the canonical `host` request. Production auto-detection semantics are unchanged.

A new regression deliberately sets `DEVTOOL_EXECUTION_ENVIRONMENT=host-android` in the parent and proves the nested command never reuses that legacy/non-canonical token. The complete canonical LIFE-G1 retained boundary is now **120/120 passed** with `ResourceWarning` promoted to error.

The v3 apply reported `rollback_state: verified` / `rollback_verified: true`; no v3 gate mutation survived.


## Qualified-host gate attempt v4 — terminal-render assertion and resumable cleanup

The fourth qualified-host attempt (`171135b2574e4afd912a6abe259fb666`) reached the intended publication-drift recovery path. Production behavior was correct: Devtool emitted a typed `result_publication_recovery` failure, preserved the user-drifted primary checkout, and kept the transaction explicit/recoverable. The gate assertion nevertheless failed because Rich wrapped the expected sentence across table rows and inserted box-drawing separators between fragments. A plain substring assertion was therefore coupled to terminal width/rendering. Gate v5 strips Unicode box-drawing characters and normalizes whitespace before checking the human-readable message, while retaining the stronger semantic assertions on nonzero exit, primary-drift reason, unchanged primary content, and surviving transaction evidence.

The same run also exposed a retained LIFE-X01 fixture-cleanup weakness after the preceding gate failure: `rollback_transaction()` correctly catches `KeyboardInterrupt`, persists a partial resumable rollback receipt, and returns control, but the fixture demanded that cleanup succeed in one invocation. Gate v5 now follows the actual rollback contract: if cleanup is interrupted and reports `resumable=true`, it immediately resumes and requires verified completion. No production rollback semantics were weakened.

The exact narrow-render condition was reproduced locally with `COLUMNS=50`; the publication-drift test and snapshot cleanup test both pass. The complete retained LIFE-G1 boundary is **120/120 passed** with `ResourceWarning` promoted to error. The v4 outer apply reported `rollback_state: verified` / `rollback_verified: true`; no v4 gate mutation survived.

A separate TEST/diagnostics defect was recorded rather than folded into LIFE-G1: when a later pytest action is interrupted before collection, the top-level failure summary can prefer `PYTEST_NO_TESTS` even when an earlier suite already collected tests and produced the primary failure. That diagnostic precedence is outside LIFE ownership.
