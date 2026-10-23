# RM20 — whole-repository in-place onboarding and affected-target inventory

Source-bound follow-up to RM17/RM18 staged adoption. RM19 repository splitting/extraction remains explicitly **cancelled**.

## Operator commands

```sh
devtool -r /absolute/consumer onboard inventory --json
devtool -r /absolute/consumer onboard plan --json
devtool -r /absolute/consumer onboard apply --reviewed-sha256 <plan.review_sha256> --yes --json
```

Both inventory and plan are **read-only** and emit the full Git-tracked path inventory, target-to-path ownership, missing/shared ownership, target-dependency edges and review issues. They do not write `.devtool/private` runtime catalogs, run builders or install dependencies. `apply` is for a repository lacking `.devtool.toml`, not an automatic migration of an existing config. The plan review digest binds HEAD, tree, branch, tracked path inventory, inferred ownership and candidate config bytes. Re-run plan after any source change.

## Promise-by-promise implementation disposition

| Promise | Disposition | Evidence / limits |
|---|---|---|
| In-place whole-repo onboarding, no extraction | DELIVERED | Direct Git root discovery + add-only `.devtool.toml`, no relocation, archive manipulation or changes to project content. |
| Reviewable, provenance-bound inventory | DELIVERED | Every tracked path classified owner(s), ambiguous/shared/unowned and nonregular Git modes; source HEAD/tree/branch + digests. See `tests/test_rm20_onboarding.py`. |
| Polyglot target discovery | DELIVERED conservatively | Python, Node, Go, Rust and Gradle/Android manifest roots; nested/root-overlapping/conflicting manifests refuse auto-generation. Unknown languages require explicit config. |
| Source-bound safe application | DELIVERED | Exact reviewed SHA required with `--yes`; clean named-branch Git repository; existing config, changed HEAD, ignored config, symlink, submodule and nonregular paths protected. One `.devtool.toml` commit; on pre-commit error, index and file restored. |
| Native Devtool DAG/affected selector reuse | DELIVERED | Generated targets parsed through canonical `load_project_config`. `analyze_affected_targets` selects all candidates for unowned/shared files. No competing task planner. |
| Existing-project inventory without overwrite | DELIVERED | Actual canonical config loaded; per-target root/job input path ownership computed from existing affected-target implementation; no mutation. |
| Canonical CLI, completions and help | DELIVERED | Checked-in `core_cli_schema.json` is authority; `share/core-generated` regenerated. |
| Tested cross-platform portability | LOCALLY QUALIFIED | Pure stdlib, subprocess Git, no `/bin/bash` or platform-specific commands in RM20 source; real Termux acceptance remains external. |

## Explicit safety/qualification boundaries

- Unknown ownership is not proof that a file is irrelevant. Shared/unowned changes must fan out to **all candidate targets** through native affected selection. Do **not** treat inferred ownership alone as evidence for restricting publication or builds.
- Discovery is **not** an automatic build/test workflow generator. It never guesses or executes shell commands; jobs and workflow steps need author review after onboarding. An existing repository config is not overwritten or silently migrated; use RM17 for legacy migrations.
- Git-tracked path inventory excludes ignored/untracked files by definition; apply itself refuses a dirty worktree. Files at ignored paths must be added to Git or authored explicitly; `onboard` never claims repository filesystem exhaustiveness.
- A successful one-repository commit is **not** a multi-repository atomic transaction, hosted release, device test or external consumer qualification. Real producer/consumer receipts remain separate gates.
- RM19 extraction is not a hidden dependency. No source splitting, directory restructuring, release artifact creation or remote push occurs.
- Git ref publication during the one-file commit is controlled by native Git; no cross-process filesystem lock or CAS across a concurrent writer is claimed. Concurrent checkout mutation is refused at the pre-publication check, while a narrowly timed race with outside Git writers is not certified away.

## Regression evidence

- Real disposable Git tests cover plan-only no mutation, supported polyglot target config, native affected fallback, source/head/dirty/detached/ignored/unsupported Git path rejection, existing-config retention, a single clean commit, idempotent refusal, and commit-failure rollback.
- Source-bound Devtool overlay transaction/rollback acceptance is recorded in the outer ZIP. A successful local fixture is not user-device installation evidence.
