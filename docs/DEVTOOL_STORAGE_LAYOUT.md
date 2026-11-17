# Devtool storage layout v1 — runtime vs project source

The repository's **source of truth** remains ordinary Git-tracked files such as
`.devtool.toml`, `.github/workflows/`, `config/delivery-required-ci.json`, tests,
manual artifact manifests and program source. `**/.devtool/` is a **generated
workspace**, never a configuration store or an operator's backup location.

## Durable ownership and paths

`devtool.storage_layout` is the shared read/write path authority. It uses the
existing XDG helpers and existing `DEVTOOL_XDG_DIR` override; no new environment
variables were invented. Repository identity is the canonical absolute worktree
path plus a bounded SHA-256 component, preventing collisions between sibling
worktrees and equally named repositories.

| Family | Owner | New default | Prior authority (still readable) |
|---|---|---|---|
| RUN v3 catalog | CORE/RUN | `$XDG_STATE_HOME/devtool/repositories/<key>/run-v3` | `.devtool/private/run-v3`, old override RUN root |
| Android validation sessions | TEST | `$XDG_STATE_HOME/devtool/repositories/<key>/android-validation` | `.devtool/state/android-validation` |
| Artifact registry | LIFE | `$XDG_STATE_HOME/devtool/repositories/<key>/artifact-registry` | `.devtool/artifacts` |
| Cleanup receipts | LIFE | `$XDG_STATE_HOME/devtool/repositories/<key>/cleanup-receipts` | `.devtool/private/cleanup/receipts` (read-only historical evidence) |
| Backup-history archives | LIFE | `$XDG_DATA_HOME/devtool/repositories/<key>/backup-history` | `.devtool/history/backups` |

`$XDG_STATE_HOME` and `$XDG_DATA_HOME` default to `~/.local/state` and
`~/.local/share`, respectively. `DEVTOOL_XDG_DIR` retains its existing behavior
of overriding both roots as a single private runtime base. The existing
`DEVTOOL_VALIDATION_STATE_REPO_ROOT` environment override still selects its
historical guest-visible validation path when explicitly supplied.

Non-durable outputs (builds, packages, temporary Python homes, test reports,
published build artifacts and local provider caches) may remain beneath
`.devtool/` where the runner needs them to be guest-visible or relative to the
worktree. They are ignored by the repository's `.gitignore`. The existing
external LIFE transaction root, RUN history, Git-private registries and
delivery-session locations remain under their existing authorities.

**No automatic relocation of active state.** If prior catalog, validation,
registry or backup data exists in the old location, that location remains in
use until explicit migration. If both old and new locations contain data
without a verified migration marker, Devtool refuses to choose a potentially
wrong authority. Cleanup receipts are an exception: new writes always go to
XDG, and recovery checks both XDG and legacy receipts.

## Inspect before migration

These commands operate on the selected checkout and do not create `.devtool`:

```sh
python3 -B -m devtool.storage_layout audit --repo "$PWD"
python3 -B -m devtool.storage_layout plan   --repo "$PWD"
python3 -B -m devtool.storage_layout ignore --repo "$PWD"
```

The audit reports the current/root per family, legacy destinations, conflicts,
unknown `.devtool` families, the **actual Git ignore status** and already-tracked
runtime files. Tracked `.devtool/` paths are blockers, never false-safe migration
successes. The distinct `plan` action enumerates each family, proposed action,
source/destination, bounded estimated bytes/file counts, existing conflicts,
blockers and exact mutation intent without creating any state directory. The `ignore` subcommand prints a read-only ignore recommendation. The inventory
is classification only, **never deletion permission**. Unknown families require
human review; Devtool never assumes that a directory named like a cache is safe
to remove without LIFE-generated ownership/protection evidence.

## Migrate safely and explicitly

First stop RUN activity, validation and native transactions. Keep backups of
any important evidence. Then:

```sh
python3 -B -m devtool.storage_layout migrate --repo "$PWD" --yes
```

The migrator rejects active transaction/session evidence, symlinked entries,
unsafe directory conflicts, mutated sources, bounded-size violations,
tracked local files, in-repository XDG overrides and ambiguous authorities.
The canonical RUN catalog and bounded stream, validation event/state/control,
artifact-registry, backup and cleanup writers acquire a shared repository-scoped lock and revalidate authority inside it.
The migrator holds the exclusive lock during copy, verification and marker
publication; post-marker drift withdraws the marker and refuses the migration.
Stop older executables that do not participate in this lock before migrating.
Destination directories are mode 0700 (including children) even if legacy
sources were 0755; original source bytes and file modes are preserved. It copies each old family into a private XDG staging
directory, hashes source and copy, atomically publishes the new directory and
writes a durable migration marker. It does **not** delete the original bytes,
reset Git history, rewrite user source, rewrite tracked ignores or remove
recovery receipts. Receipt histories can be merged by exact non-conflicting
filenames; conflicting receipt contents refuse without overwrite.

If interrupted after publication but before the marker, the operation is
resumable only when source and published destination contents verify equal.
An uncooperative legacy writer producing drift causes a fail-closed conflict,
not a successful cutover. Such conflicts require manual review; never use the
existence of a copy as proof that it is authoritative.
Read-only audit can be used to examine drift. Migration of very large or
platform-specific storage may need manual inspection; never blindly `rm -rf`
the old `.devtool/private`, `.devtool/state`, or `.devtool/history`.

## Git hygiene

Recommended project `.gitignore`:

```gitignore
# Devtool-generated local workspaces, never project configuration
**/.devtool/
```

The root `.devtool.toml`, generated configuration under ordinary `config/`,
metadata in `.devtool-artifact/`, and explicitly authored artifact manifests
remain eligible for tracking. Avoid unignoring `**/.devtool/artifacts.d/` under
a blanket directory exclusion: Git will not traverse an ignored parent.
Instead explicitly export curated history to a normal tracked directory.

Existing tracked `.devtool/` files remain tracked after adding ignore rules;
inspect `git ls-files -- .devtool` and review each entry before proposing an
index change. The migration never runs `git rm`, `git clean`, `reset`, rebase,
force-push or replaces the native Devtool transaction engine.

## Safety and operator acceptance

Before rollout, run all affected RUN, LIFE, validation, cleanup, artifact,
Python/native runner, Git dirty-state and native launcher tests, including
negative tests. Real Termux/chroot and hosted acceptance are independent from
local tests. Old read paths and non-secret receipts remain until an explicit
future deprecation, after safety evidence and documented migration coverage.

Native launcher **always runs in plain terminal mode**; the migration module
never enables Textual apply/execution hosts, disables CLI authority, or copies
anything to the clipboard.

## Cross-filesystem backup archives

Native LIFE retains sole authority for the backup archive operation. On an
`EXDEV` rename error, it copies into a temporary destination on the destination
filesystem, verifies file or directory content, fsyncs, publishes the copy,
reverifies the original, and atomically retires the original into a same-volume
recovery tombstone before removing it. If verification or retirement fails it
reports failure and retains originals or recovery material instead of claiming
a successful deletion. Git-tracked source remains protected by existing LIFE
safety rules. No cross-device archive is used to apply or validate overlays.

## Native package check-only

The source kit's `--inspect` command is archive-only. A native `check` from an
existing feature worktree can build a pinned temporary native package and run
Devtool's native overlay validator without creating a branch or worktree.
From `main`, check-only refuses; only the explicit `--apply` route is permitted
to prepare a separate feature worktree. Never replay a committed apply after
CI failure: resume its native delivery session.
