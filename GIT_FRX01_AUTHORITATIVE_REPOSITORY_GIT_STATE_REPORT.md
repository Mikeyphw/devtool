# GIT-FRX-01 — Authoritative repository Git-state model

## Scope

This overlay implements **GIT-FRX-01 only** from the Devtool Git + Federation roadmap. It establishes the read-only Git-state boundary consumed by later transaction, amend/fixup, federation-planning, recovery, and remote Git overlays. It intentionally does **not** create private Devtool refs/journals, transaction worktrees, commits, overlay rebases, or federated Git integration.

Baseline: the authoritative Devtool source after `devtool_frx_full_roadmap_reseal_v1` is replayed onto `devtool-20260924-195855-674441.tar.gz`.

## Delivered contract

New public surface:

```text
devtool git state
devtool git state --json
```

The state snapshot now records:

- canonical Git repository root and requested canonical root;
- stable local Git repository ID shared by linked worktrees;
- existing FRX `RepositoryIdentity` for federation/enrollment compatibility;
- HEAD SHA, symbolic ref/branch, detached and unborn truth;
- configured/resolved/partial upstream state;
- remote-tracking refs that contain HEAD as protected-history evidence groundwork;
- canonical index tree SHA and index-entry fingerprint;
- staged, unstaged, untracked, and unmerged paths, including rename provenance;
- merge, rebase, cherry-pick, revert, and bisect state;
- current worktree identity plus all worktrees registered in the common Git directory;
- linked-worktree identity stable across `git worktree move`;
- submodule relationships, including paths containing spaces and submodule checkout normalization;
- shallow and partial-clone/promisor state;
- SHA-1/SHA-256 object format and Git capability evidence;
- a single `dirty` summary derived from explicit source-state categories.

Machine output remains secret-neutral: remote URLs/credentials are not exposed. Existing FRX remote URL fingerprints remain digests only.

## Read-only guarantee

GIT-FRX-01 is pre-transaction state discovery. The collector therefore must not mutate repository state.

The final implementation isolates both surfaces touched by `git write-tree`:

- `GIT_OBJECT_DIRECTORY` points to a temporary object database;
- `GIT_INDEX_FILE` points to a temporary copy of the repository index;
- the real object database is readable only through Git's alternate-object mechanism.

Regression evidence proves that state collection leaves HEAD, refs, the real index bytes, and repository object files unchanged.

## Git + federation boundary

The Git-state collector extends rather than replaces FRX identity:

- FRX `RepositoryIdentity` remains the enrollment/config ownership identity.
- `repository_id` identifies one local Git common-directory repository instance across its linked worktrees.
- an unenrolled nested Git checkout discovered from the CLI is resolved using Git's nearest repository boundary instead of borrowing an ancestor `.devtool.toml`.
- cross-repository longest-enrolled-root ownership remains reserved for GIT-FRX-07.

This separation prevents GIT-FRX-01 from prematurely duplicating or weakening FRX ownership semantics.

## Audit loop

The implementation was repeatedly restarted against the original GIT-FRX-01 roadmap promises.

### Pass 1

Found and fixed:

- missing top-level completion/help synchronization;
- missing canonical/symlink regression;
- missing explicit unmerged-index evidence.

### Pass 2

Found and fixed:

- `git write-tree` could create repository tree objects;
- nested Git CLI discovery could be captured by an ancestor Devtool config root;
- core staged/unstaged/untracked/index/remote-ref/submodule probes could degrade some Git failures into empty state;
- configured-but-unresolved upstream state needed to be distinct from no upstream;
- submodule paths containing spaces needed lossless parsing;
- linked-worktree ID needed Git administrative identity instead of filesystem path identity.

### Pass 3

Found and fixed:

- submodule checkouts can make `git worktree list --porcelain` report the submodule Git administrative directory instead of the actual `core.worktree`; the state model now normalizes that primary worktree without changing linked-worktree semantics;
- corrupt symbolic HEAD/ref state could be mistaken for an unborn branch; HEAD probing now distinguishes normal detached/unborn states from repository corruption;
- optional Git config probes now distinguish “not configured” from command failure;
- the previous isolated-object `git write-tree` still mutated the real index cache-tree extension. The final implementation also isolates the index using `GIT_INDEX_FILE`.

### Final restart

The final promise-by-promise restart found **zero new material GIT-FRX-01 gaps**. No later-roadmap behavior was pulled forward.

## Verification

Focused milestone and documentation/completion contract:

```text
36 passed, 32 subtests passed
```

Adjacent FRX / federation / CLI / completion / architecture consumer regression selection:

```text
294 passed, 5 deselected, 8 subtests passed
```

The five deselected tests are pre-existing post-FRX-baseline failures and were reproduced on the untouched baseline before exclusion:

- `tests/test_cli.py::test_apply_overlay_continues_after_successful_stale_rollback`
- `tests/test_cli.py::test_bundle_reuses_successful_dotnet_validation_for_same_workspace`
- `tests/test_cli.py::test_sequential_overlay_flow_uses_auto_repair_before_rollback`
- `tests/test_arch_final_seal.py::test_golden_build_report_contract`
- `tests/test_rem03_cli_config_discovery.py::test_feature_flags_and_doctor_use_explicit_config_path`

The first three are stale mocks that do not accept the already-existing `tasker_progress=` keyword. The build-report golden is stale relative to existing TLJ/FRX machine-contract fields. The explicit-config test conflicts with the already-existing FRX config-ownership boundary. None originates from GIT-FRX-01.

Additional checks:

```text
git diff --check: PASS
python -m compileall: PASS
manual devtool git state: PASS
manual devtool git state --json: PASS
```

## Overlay validation policy

GIT-FRX-01 is **not** one of the campaign's three full validation gates. The artifact therefore declares validation disabled/deferred and must be applied with `--no-validate`.

Full validation remains reserved for GIT-FRX-06, GIT-FRX-16, and GIT-FRX-18.

The before-commit finalize hook refreshes the installed Devtool source with:

```bash
pip install -e ~/Code/devtool
```

while preserving/restoring `devtool.egg-info` packaging metadata.

## Exact-base artifact replay

After packaging, the artifact contract was preflighted with Devtool against a fresh directory named `devtool` containing the post-FRX authoritative baseline:

```text
Artifact validation passed.
```

A clean replay then verified:

```text
source preconditions: 10 / 10
post-apply payload hashes: 10 / 10
focused replay tests: 36 passed, 32 subtests passed
```

No source changed between the final zero-gap GIT-FRX-01 audit pass and this replay other than the audit report itself being finalized for packaging.
