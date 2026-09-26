# GIT-FRX-16 — Git-bundle transport and remote repository attestation — Gate 2

## Scope

GIT-FRX-16 implements the Phase F remote Git federation boundary without absorbing GIT-FRX-17 audit/rerere/doctor work or GIT-FRX-18 signing/final-seal work.

The roadmap promise is implemented as a first-class source transport contract:

- `source_transport = auto | git-bundle | rsync`;
- clean frozen federated Git commits are eligible for managed bundle transport;
- strict `git-bundle` fails closed and never silently degrades to rsync;
- `auto` selects bundle only when both the frozen source contract and remote worker Git capability permit it, otherwise it selects rsync before transfer;
- existing/unmanaged remote checkouts remain outside bundle materialization ownership.

## Git object transport

`devtool.federation_git_transport` owns the Git-specific source transport contract. For an eligible frozen child it:

1. verifies the frozen start commit/tree and source identity;
2. creates a private temporary `refs/devtool/transport/...` ref for an explicit bundle closure;
3. creates and locally verifies a bundle and records its digest/size/ref/source tree;
4. removes the private local transport ref after bundle creation;
5. transfers the bundle through the existing SSH transport boundary;
6. verifies the transferred bundle digest and `git bundle verify` remotely;
7. fetches the private transport ref into the managed remote repository;
8. materializes the exact frozen detached checkout and verifies HEAD/tree;
9. verifies selected-config digest and normalized Git remote identity where declared;
10. writes remote federation attestation evidence and removes the transferred bundle.

The implementation deliberately does not use global `git bisect` state or introduce a second repository-sync authority. FRX-Y11 remains the frozen source-lock/bootstrap authority; SSH remains transport/execution infrastructure.

## Remote repository attestation

Remote child attestation now proves the roadmap-required identity fields at the intended top-level re-entry boundary:

- repository ID / effective repository fingerprint;
- expected frozen start commit;
- normalized Git remote identity digest when policy uses it;
- selected `.devtool.toml` / config digest;
- expected federated node/reference identity;
- parent run ID and parent node ID;
- child run ID.

Nested FRX child discovery continues to disable outer parent-config/attestation consumption, preventing an outer child attestation from being misapplied to a nested repository.

Managed `~` and `$HOME` workspace paths continue to be expanded by the existing remote shell boundary rather than locally interpolated into host paths.

Returned producer provenance includes requested/effective source transport and the transport evidence digest together with the federation attestation fields, so artifact evidence can be tied back to the exact remote source materialization.

## Gate audit findings and repairs

The audit loop found and repaired material gaps before sealing:

- strict `git-bundle` initially degraded to rsync for non-federated execution; it now fails closed because no frozen federated source contract exists;
- `auto` could initially choose bundle before checking remote Git capability; it now selects rsync before transfer when the worker lacks Git, while strict bundle fails before transfer;
- returned producer identity initially omitted the bundle/attestation evidence digest; provenance now carries it;
- the Gate-2 remote suite exposed an older OCI-06 persisted-history row format that current SSH history parsing could misalign after newer fields were added. A compatibility branch now parses that historical 18-column record without changing current-record semantics.

No GIT-FRX-17 or GIT-FRX-18 behavior was added.

## Construction qualification

The restart-at-01 Gate-2 audit completed with:

- **860 passed + 7 subtests**, exit 0, across GIT-FRX-01..16, FRX/X/Y federation suites, SSH/SSHW, chroot/PRoot, and OCI remote/provider suites;
- focused post-repair GIT-FRX-09..16 + FRX remote audit: **80/80 passed**;
- SSH audit block: **206/206 passed**;
- chroot/PRoot/OCI audit block: **232 passed + 7 subtests**;
- source syntax compilation: PASS;
- Git-bundle/attestation fixture: PASS, with private transport ref cleanup and no global Git-bisect state.

## Native Devtool qualification

`roadmap-git-frx16` plans and preflights as a 9-node declared Devtool workflow. The composite invocation reproduced the already-known outer workflow return/lifecycle hang after successful early stages, so remaining declared stages were qualified independently through Devtool rather than discarded or replaced with ad-hoc validators.

Declared stage evidence:

- GIT-FRX-15: **10 passed**;
- FRX-Y11: **14 passed**;
- SSH-03: **19 passed**;
- GIT-FRX-16: **15 passed**, 0 warnings/errors;
- SSH-03 regression stage: **192 passed**, 0 warnings/errors;
- workflow/EXO baseline: **18 passed**, 0 warnings/errors;
- editable-install gate job: PASS using `python -m pip install --no-build-isolation -e <transaction-root>`;
- bundle/attestation fixture: PASS;
- source syntax: PASS.

The gate artifact performs bounded fail-closed acceptance validation in the isolated transaction candidate and uses a `before_commit` finalizer to re-point the editable install at `DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT`, preserving the proven SSH-02/SSH-03 lifecycle and preventing cleanup from leaving the installed Devtool bound to a disposable transaction worktree.

## Gate semantics

This is **GIT-FRX-16 Gate 2**. The artifact requires validation and is intended to be applied **without `--no-validate`**. Its apply-time validation contract is fail-closed and covers source syntax, editable-install lifecycle, bundle/attestation, critical federation operational boundaries, frozen bootstrap, cancellation/recovery, SSH lifecycle, OCI resilience, and workflow/EXO regressions. The complete restart-at-01 Gate-2 sweep remains construction/replay audit evidence rather than being redundantly rerun on every Termux apply.

Replay qualification and exact payload equivalence are recorded separately in the final audit evidence bundle after sealing.

## Exact-package replay repair (v2)

The first sealed v1 package passed construction Gate-2 coverage but its clean extracted-tree replay exposed a packaging-sensitive defect: `create_federated_git_bundle()` ran `git bundle verify` without binding the command to the source repository. Construction happened from a Git working directory, masking Git's requirement that bundle verification run with repository context. In a clean extracted replay tree the declared bundle fixture therefore failed with `need a repository to verify a bundle`.

v2 repairs the implementation rather than weakening validation: local bundle verification now executes as `git -C <source-root> bundle verify <bundle>`. A dedicated regression changes process CWD to a non-repository directory and proves bundle creation/verification remains bound to the frozen source repository. The standalone fixture now passes from the clean replay tree.

Post-repair focused replay evidence:

- `tests/test_git_frx16_git_bundle_remote_attestation.py`: **16/16 passed**;
- `tests/test_frx09_remote_repository_identity.py`: **11/11 passed**;
- `tests/test_frx_full_roadmap_reseal.py`: **9/9 passed**;
- `tests/test_devtool_roadmap_native_execution_contract.py`: **4/4 passed**;
- standalone Git-bundle/attestation fixture: PASS from a non-Git extracted repository tree;
- source syntax compilation: PASS.

The v2 delta is limited to the bundle-verification repository binding, its regression test, and this report. All Gate-2 behavior and validation coverage remain otherwise unchanged.


## Apply-time isolation and performance repair (v3)

A real Termux Gate-2 apply exposed two issues that clean construction runs did not reproduce:

1. `tests/test_frx10_operational_boundaries.py` inherited `DEVTOOL_VALIDATION_STATE_REPO_ROOT` from the enclosing artifact-validation transaction. Its synthetic parent/child sessions therefore looked at the live outer Gate-2 validation session and failed with `Validation session ... is already active`. The tests now explicitly remove that outer redirect before creating synthetic validation sessions, preserving the intended child-owned-state assertion while remaining hermetic under artifact validation.
2. The v2 manifest reran broad historical FRX/X/Y suites during apply. On Termux, several legacy end-to-end tests take 20-50 seconds each, making the first FRX block take nearly eight minutes before interruption. Gate-2's full restart-at-01 audit remains authoritative construction evidence (**860 passed + 7 subtests**), while v3 apply-time validation is intentionally bounded to high-signal boundary tests.

The v3 acceptance set covers:

- GIT-FRX-16 bundle transport and remote attestation;
- FRX-10 operational ownership/session isolation;
- FRX-09 remote repository identity;
- FRX-Y11 frozen-lock bootstrap/sync;
- GIT-FRX-14 cancellation/recovery;
- SSH-03 lifecycle hardening;
- OCI-06 resilience/history compatibility;
- workflow/EXO baseline.

On the replay host this acceptance set is **77/77 + 18/18**, with the core cross-section completing in ~6 seconds and the EXO baseline in ~3 seconds. This change reduces redundant apply-time cost without weakening the already-completed full Gate-2 audit or allowing `--no-validate` for Gate 2.
