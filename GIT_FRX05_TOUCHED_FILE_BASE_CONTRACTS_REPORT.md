# GIT-FRX-05 — Touched-file base contracts

## Scope

This non-gate milestone adds authoritative overlay base identity without crossing the GIT-FRX-06 Gate 1 boundary. It remains standalone because GIT-FRX-06 introduces three-way rebase/equivalence and therefore has a separate validation and recovery surface.

## Delivered contract

Overlay schema-v2 manifests may declare:

- `base_commit`
- `base_tree`
- `base_contract_mode = exact-tree | touched-files`
- `touched_paths[]`
- `source_sha256[path]`
- `expected_post_sha256[path]`

`exact-tree` is commit-strict and rejects unrelated committed or worktree drift. `touched-files` uses the declared Git tree object as the portable base authority and permits unrelated drift while requiring every touched path to match its base type, executable mode, file contents or symlink target, and add/delete state. Source SHA-256 values are path identities that do not follow symlinks. Post hashes must match the artifact payload; deletes use `null` post identity and adds use `null` source identity.

For touched-files portability, `base_commit` remains provenance and is verified when available. If the same base tree exists in an equivalent checkout under a different commit SHA, `base_tree` is sufficient to verify the touched paths safely. Exact-tree still requires the exact base commit.

Legacy inventory-only artifacts remain supported.

## Integration

- `validate_artifact()` validates base-contract shape and source state.
- Inventory conflict/source hashing is symlink-safe.
- Existing overlay rebase support refreshes a declared base contract after rebasing.
- Artifact inspection and overlay manifest summaries expose base-contract identity.
- `python -m devtool.artifact_base_contract fixture` is the reusable TLJ fixture.
- `materialize --repo ... --inventory-json ... --mode ...` emits a base-contract manifest fragment.
- Native roadmap workflow/test: `roadmap-git-frx05` / `test:roadmap-git-frx05`.
- `roadmap-current` now includes GIT-FRX-05.

## Audit loop

Focused GIT-FRX-05 + roadmap contract: 19 passed during development; first-class JUnit run: 15 passed.

Compatibility groups after the portability fix:

- artifact/transaction/GIT-FRX-02..05: 202 passed;
- Git state / FRX-Y01 / EXO roadmap contracts: 59 passed;
- symlink/rebase compatibility spot check: 22 passed.

One `test_arch_final_seal.py::test_golden_build_report_contract` failure was reproduced identically on the untouched GIT-FRX-04 baseline. Its diff is stale golden UI/machine-output fields introduced before this milestone, so it is recorded as pre-existing and not attributed to GIT-FRX-05.

Native plan resolves `roadmap-git-frx05` to 5 nodes including 2 target-local jobs. Preflight is structurally healthy except for the expected `native-termux` target versus `desktop-linux` analysis-host incompatibility.

## Gate/lifecycle policy

This is not a validation gate. The artifact intentionally contains no finalize hook and does not reinstall Devtool. GIT-FRX-06 remains Gate 1 and will own the first full campaign validation/reinstall boundary.
