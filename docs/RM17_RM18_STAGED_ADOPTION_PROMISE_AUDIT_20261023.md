# RM17 + RM18 — merged staged-adoption implementation and audit

## Merge-window decision

RM17's repository-scoped composite config/overlay/wrapper/.gitignore transaction is the exact immutable unit required by RM18's prepare-before-publish, so these milestones are delivered together rather than stacking a serial "commit first, stage later" migration. Existing `adopt-batch plan|apply|resume|status` is kept for compatibility; **new deployments should use staged operations**. Nothing automatically extracts repositories: RM19 remains cancelled.

## Commands (all source-locked)

```
devtool adopt-batch review-staged /absolute/adoption.json --state-dir /external/state
devtool adopt-batch upgrade-review /absolute/adoption.json --state-dir /external/state
devtool adopt-batch prepare /absolute/adoption.json --state-dir /external/state --yes
devtool adopt-batch publish /absolute/adoption.json --state-dir /external/state --yes
devtool adopt-batch resume-staged /absolute/adoption.json --state-dir /external/state --yes
devtool adopt-batch recovery-plan /absolute/adoption.json --state-dir /external/state
devtool adopt-batch recover /absolute/adoption.json --state-dir /external/state --yes
```

Manifest continues to use `devtool.multi-repo-adoption.v1`; `repositories` entries retain exact `source_head`, `source_tree`, remote identity, branch, archived overlay SHA, and optional `wrapper`, `migrate_config`. New reviewed fields: `repair_gitignore` (strict generated paths only), `reviewed_wrapper_sha256` (required to replace non-current existing launchers), and `offline_hooks` (list of built-in pure offline checks). No arbitrary hook code, install/bootstrap, shell or network action is available.

## Promise-by-promise results

| Promise | Engineering state | Evidence / important boundary |
|---|---|---|
| RM17 reversible known legacy aliases | Implemented | Existing config migration + wrapper schema v1→v2 in the same candidate; unknown top-level keys refuse except extensions/x- namespace; original Git parent is exact preimage, staged rollback uses a commit lease. |
| RM17 single-repo composite transaction | Implemented in staged mode | `_desired` projects legacy config, wrapper pair+lock, overlay and generated-only `.gitignore`, checks path ownership, hashes; private Git index constructs one commit. |
| RM17 pristine/installed/uncommitted/partial/drifted detection | Partial by design: two paths | Legacy `plan` retains detailed FRESH/APPLIED_UNCOMMITTED/PARTIAL/DRIFTED classification; staged mode requires **clean pristine** source and prepared/published branch leases. It never implicitly adopts an uncommitted dirty tree. |
| RM17 source-changed upgrade review | Implemented as read-only proposal; manual review required for apply | `upgrade-review` computes original-base/current/incoming three-way candidate digest and classifies conflict. Only a new explicitly reviewed source-bound overlay may be prepared; no auto-overwrite. Existing `devtool overlay rebase` can author reviewed artifact. |
| RM17 offline hooks and recovery coordinator | Implemented with bounded scope | Fixed offline built-in hooks, native overlay-journal blocker check, `recovery-plan` with safe next action. Does not execute arbitrary historical retirement automatically. |
| RM18 preflight-all before target-branch mutation | Implemented | All original HEAD/trees, exact overlay SHA/preimage, branch/remote, clean worktree, unknown paths and native recovery markers checked before staging; all prepared refs validated again immediately before first CAS publish. |
| RM18 isolated commit staging | Implemented | `GIT_INDEX_FILE` private index, Git blob/tree/commit objects, namespaced prepared refs; no target branch, index or worktree touched in prepare. |
| RM18 per-repository CAS publication | Implemented | `git update-ref refs/heads/... new expected-old` after global barrier; `read-tree -u` synchronizes checked worktree/index. All receipts explicitly `cross_repository_atomic=false`. |
| RM18 crash recovery + truthful partial | Implemented | durable atomic journal fsync, `PARTIAL_COMMIT`, resumable completed/checkpointed refs, all-ref preflight before compensating rollback; newer HEAD, unknown working changes or stale journal block compensation. Prepared refs remain as recovery evidence. |
| RM18 idempotence | Implemented | repeated publish does not construct another commit or move refs when already qualified, and verifies prepared refs and candidate source identity. |

## Remaining boundaries, not disguised as qualification

- Git cannot atomically swap refs in distinct repositories. A failure after publishing one is a **PARTIAL_COMMIT**, not a global success. There is no remote push or forced reset.
- The classic `adopt-batch apply` path remains source-guarded but sequential, for existing callers. The new `publish` path is recommended for RM18 guarantees.
- `upgrade-review` returns a candidate hash/status but deliberately does not auto-execute unreviewed merges. A reviewed rebased overlay and new source-bound manifest are a precondition for applying source-changed candidates.
- Tests use disposable Git repositories; real studyflow and Telegram migrations and external consumers still need project-specific acceptance. No release/signing/device qualification is inferred.

## Engineering evidence

- `tests/test_rm17_rm18_staged.py` exercises prepare barrier, private refs, two-repo publication, idempotence, wrong/dirty host/source refusals, partial publication/compensation, advanced-HEAD denial, read-only upgrade, extension preservation and offline hook rejection.
- `tests/test_next05_adoption_batch.py` keeps the legacy adoption behavior and resume intact.
- Canonical CLI parser and machine help are generated from `core_cli_schema.json` and checked against `share/core-generated`.
- Full native Devtool overlay acceptance and rollback are recorded in the downloadable bundle acceptance report. Termux on-device installation is not simulated as accepted.
