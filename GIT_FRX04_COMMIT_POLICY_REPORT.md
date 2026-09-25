# GIT-FRX-04 — Commit Policy: new / amend / fixup / none

## Roadmap identity

- Roadmap ID: **GIT-FRX-04**
- Devtool target: `devtool`
- Native workflow: `roadmap-git-frx04`
- First-class declared test: `roadmap-git-frx04`
- Non-test operations: `commit-policy-fixture`, `signing-policy-probe`
- Apply policy: **non-gate / `--no-validate` / no Devtool reinstall**

## Merge-window decision

The campaign evaluated **GIT-FRX-04 + GIT-FRX-05 + GIT-FRX-06**. GIT-FRX-06 is Gate 1 and is not merged across. GIT-FRX-04 remains standalone because it changes Git history semantics while GIT-FRX-05 changes artifact base/schema identity; combining both independent safety transitions would materially worsen fault isolation.

## Promise trace

| Promise | Delivered contract |
|---|---|
| Four commit modes | `--commit-mode new|amend|fixup|none`; aliases `--amend` and `--no-commit`; `--fixup-target` selects the fixup revision. |
| Recoverable amend | Amend requires and verifies the GIT-FRX-02 `backup-head` private ref before rewriting HEAD. |
| Published-history safety | HEAD reachability is independently checked against authoritative upstream/protected remote refs. Published amend is refused unless `--allow-published-rewrite` is explicit. |
| Merge safety | Merge-commit amend is refused unless `--allow-merge-amend` is explicit. |
| Author policy | Amend preserves the original author by default; `--reset-author` opts into the current identity. |
| Signing policy | `commit.gpgSign` is observed and recorded. Commit execution never disables signing, so configured signing remains authoritative for new/amend/fixup commits. |
| Fixup repair path | Fixup creates a `fixup! <subject>` child commit and never rewrites the target commit. |
| Evidence migration | Existing `refs/notes/devtool` evidence is copied from the superseded commit to the amended result while retaining the old note. |
| Supersession provenance | Transaction journal records `mode`, result SHA, `supersedes_commit`, fixup target, signing requirement, published-rewrite override, and `automatic_push=false`. |
| Never push | No policy or runner path invokes `git push`; explicit published rewrite remains local-only. |
| Isolated worktree compatibility | Amend/fixup execute in the GIT-FRX-03 transaction worktree and integrate only the exact result after primary-state verification. |
| Deferred validation | Commit mode, fixup target, author/reset and safety overrides persist in durable validation context and survive process exit/resume. |
| Legacy compatibility | Internal callers that provide the historical `commit=<message>` field without `commit_mode` still infer normal `new` commit semantics. |
| Native roadmap evidence | `roadmap-git-frx04` composes syntax, published-history fixture, signing probe, GIT-FRX-03 predecessor, GIT-FRX-04 test and EXO baseline. |

## Safety behavior

Safe amend performs policy preflight before commit creation. It validates the private backup ref against current HEAD, refuses merges and published history by default, and records a machine-readable policy evidence object. The isolated-index commit runner stages only declared artifact paths, preserving unrelated staged user work.

`--allow-published-rewrite` only authorizes the local amend. The test fixture proves the configured remote tracking ref remains at the old SHA after the local rewrite.

Fixup resolves and freezes the target commit SHA before commit creation and lets Git generate the canonical `fixup!` subject. This is the non-rewrite repair path for commits that may already be shared.

## Native execution contract

`roadmap-git-frx04` resolves to six nodes:

1. `job:source-syntax`
2. `job:commit-policy-fixture`
3. `job:signing-policy-probe`
4. `test:roadmap-git-frx03`
5. `test:roadmap-git-frx04`
6. `test:roadmap-workflow-exo-baseline`

The disposable policy fixture creates a temporary repository and bare remote, publishes HEAD, proves the protected remote is recognized, and destroys the fixture. The signing probe is read-only. `plan --json` reports six nodes and three target-local jobs. On the audit host, native preflight truthfully stops at the configured `native-termux` versus `desktop-linux` environment boundary; target ownership was not weakened.

## Audit loop

Bounded regression evidence:

- GIT transaction / rollback / FRX-02/03 compatibility: **79 passed**;
- resumable validation / finalize / fail-closed compatibility: **64 passed**;
- Git FRX-01..04 + roadmap + completion contracts: **92 passed + 32 subtests**;
- log/summary/archive/hook compatibility: **65 passed**;
- validation report integrity: **6 passed**;
- selected legacy CLI commit paths: **7 passed**;
- first-class `roadmap-git-frx04`: **13 passed**, JUnit failures/errors = 0.

One selected `test_cli.py` stale-rollback test fails because its `_maybe_commit_after_apply` mock does not accept the already-existing `tasker_progress` keyword. The exact test fails identically against the untouched FRX-03 baseline, proving it is pre-existing and not caused by GIT-FRX-04.

## Gate policy

GIT-FRX-04 is **not** a validation gate. The artifact has no finalize/self-refresh hook and does not run `pip install -e ~/Code/devtool`. Gate self-refresh remains reserved for GIT-FRX-06, GIT-FRX-16 and GIT-FRX-18.
