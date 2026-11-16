# Original S7 P01 — Consumer adoption diagnosis and source-bound scaffolding

This is the first slice of **Phase 7** from the original S0–S7 roadmap. It
introduces *no* new mutation, GitHub, CI registration, or release engine.
Devtool's native overlay validation/application and existing S1–S6 delivery
remain authoritative. Never execute source plan values as shell commands.

## Commands (Python 3.11+; inside Devtool checkout)

```zsh
python3 -m devtool.delivery_adoption audit \
  --repo "$HOME/Code/consumer" --json
```

The read-only audit checks exact Git worktree/branch/source identity, origin,
worktree cleanliness, and bounded launcher filenames that *might* duplicate
GitHub CI/delivery orchestration. Its heuristic hints **are not proof** of
redundancy, test coverage, migration success, or release readiness. No remote
API calls or repository mutations occur.

Prepare a private payload directory outside the target repository with changed
file bytes, e.g. `$TMPDIR/my-payload/README.md`. Then create a data-only plan:

```json
{
  "schema_version": 1,
  "changes": [
    {"path":"README.md", "action":"modify"},
    {"path":"docs/new-guide.md", "action":"add"}
  ]
}
```

Only `add` and `modify` are supported by this initial generator. It refuses
deletions, shell commands, symlinks, missing source blobs, dirty worktrees,
unsafe paths, >32 entries, >1 MiB/file, >4 MiB total and output overwrite.

```zsh
repo="$HOME/Code/consumer"
sha="$(git -C "$repo" rev-parse HEAD)"
python3 -m devtool.delivery_adoption scaffold \
  --repo "$repo" --payload "$TMPDIR/my-payload" \
  --plan "$TMPDIR/overlay-plan.json" \
  --output "$HOME/Download/consumer-s7.zip" \
  --expected-head "$sha"
```

The generator emits one deterministic Devtool v2 overlay ZIP, complete with
per-file SHA-256 digests, source SHA-256 for every modification,
Devtool GIT-FRX-05 `exact-tree` base contract (base commit, tree, touched paths
and before/after checksums), exact branch,
canonical remote, conflict policy `fail`, and atomic-apply contract. Generated
ZIPs are **not** automatically applied or pushed. Before applying to any
consumer, use the actual native `devtool validate-overlay` command and review
its current behavior tests. Apply only with explicit operator authorization
through Devtool's existing transaction. This S7 slice does not run local
Android Gradle and does not imply source, hosted CI, device, or release
qualification.

## S7 progression

- **P01:** bounded source-bound scaffold generator and read-only adoption audit
  (this change)
- **P02:** real lightweight consumer migration with strict source pins and
  current behavior tests; no migration before consumer selection and audit
- **P03:** Go/Android adoption and removal of verified duplicate launchers
- **P04:** cross-consumer parity and final S7 acceptance; historical evidence
  retained, redundant executable tests retired only after proof

Every consumer migration must preserve the original repository's own source
identity, PR, required CI policy, and explicit release boundaries. A test
fixture is not evidence that any real consumer is already migrated.

## Termux output publication (P01 HF01)

Some Android/Termux Python builds omit `os.link`. The scaffold publisher
uses `O_CREAT | O_EXCL` with mode `0600`, verifies the written ZIP digest,
flushes file and directory data, and refuses concurrent creation instead of
overwriting someone else's artifact. Ordinary write errors remove only the
newly created inode; a hard-killed process can leave a partial output, which
must be inspected/removed explicitly before retrying (never auto-overwritten).
The generated artifact remains data-only and no consumer repository is edited.


## V3.2 validation-aware scaffold extension

The S7 data-only scaffold now accepts optional `validation`, `preflight`, and
`finalize` plan objects alongside `changes`. Metadata scripts under
`.devtool-artifact/` must have exact SHA-256 pins and are copied as metadata,
not tracked repository payload. The native artifact validator, not S7, owns
execution. Existing two-key schema-v1 plans produce the same disabled static
manifest and are retained solely for backwards compatibility.

New native launcher packages should declare `delivery_validation_mode: full`
and a required executable validation policy. The launcher then calls native
`apply overlay --validate`. With no policy it warns that only legacy static
validation is available. A `static` plan containing declarative tasks/tests is
refused because they would otherwise be skipped. Deferred mode requires a
separate, explicitly resumable native validation session and is not enabled by
the launcher in this milestone.

See [validation-aware launcher delivery](LAUNCHER_VALIDATION_DELIVERY_V32.md).
