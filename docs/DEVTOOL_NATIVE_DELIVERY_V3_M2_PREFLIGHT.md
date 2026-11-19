# M2 — Verified runtime selection and source-bound native preflight

**Scope:** opt-in `schema_version=3` delivery contracts only. No new apply,
commit, session, GitHub, CI or rollback engine is introduced. Existing v1/v2
artifacts and immutable bootstrap profiles remain unchanged.

## Runtime qualification

The native M2 entrypoint is `python3 -B -m devtool.native_runtime_preflight`.
Its `check` and `run-verified` operations do not execute an unverified archive.
They invoke Devtool's existing artifact validator and canonical v2 executable
bootstrap only after preflight. Runtime candidates must be trusted by the
operator or lie in the active native source checkout/private runtime cache.
Candidates are checked using Git from the Python installation or system paths,
not a caller-controlled `PATH`. Git environment overrides and replacement
objects are disabled for identity checks. No network fetch or ref rewrite is
performed during candidate selection.

The resolver ranks fully verified compatible candidates by semantic version,
commit time and SHA. Explicitly selected unsafe/incompatible candidates fail
rather than silently falling back. A clean, verified detached-HEAD runtime is
allowed; a detached target is not. Runtime and target must be independent
worktrees; the caller must supply a separate verified runtime when using this
module to preflight a Devtool self-update.

Every accepted runtime must match canonical origin `Mikeyphw/devtool`, satisfy
`>=3.2.0` within ABI major 3 and have a clean worktree. Git verifies the current
commit and tree, then compares each checked-out file against the committed
Git blob for `devtool/launcher_contract_v3.py`,
`devtool/core_cli_schema.json` and this M2 preflight module. It also parses the
literal launcher version marker without importing any candidate's module.
The normalized runtime receipt contains exact commit/tree/blobs and version.

## Native artifact qualification boundary

`devtool.artifacts.validate_artifact` now calls `preflight_manifest()` for
valid opt-in v3 overlays when source-state checks are requested. This occurs
before a native apply can mutate its target. Exact target root, GitHub origin,
named branch, pre-apply HEAD and tree, clean tracked/untracked state, and
separate verified runtime are mandatory. The existing native verifier retains
full authority over file inventory, hash matching, executable tasks, rollback
and transaction semantics. A structural read-only inspection using
`check_source_state=False` does not claim a runtime or worktree qualification.

## Untrusted PYZ handling

Never invoke `python3 unknown.pyz` directly: Python executes `__main__.py`
before code inside that archive could prove its own authenticity. Use the
external native boundary instead:

```sh
python3 -B -m devtool.native_runtime_preflight check \
  --repo "$TARGET" --artifact "$ARCHIVE.pyz" \
  --trusted-runtime "$TRUSTED_DEVTOOL_RUNTIME"

python3 -B -m devtool.native_runtime_preflight run-verified \
  --repo "$TARGET" --artifact "$ARCHIVE.pyz" \
  --trusted-runtime "$TRUSTED_DEVTOOL_RUNTIME"
```

The optional `--apply` on `run-verified` is explicit local-apply permission,
not permission for push, PR, release or merge. The native check requires v3
opt-in and a canonical `devtool.executable-overlay.v2` PYZ profile with the
exact checked bootstrap bytes, digest and payload inventory. It does not import
or execute ZIP members while verifying. Before launching the canonical
bootstrap through native Devtool, it copies the verified bytes into a private
`0700` directory with an exclusive `0600` file, rechecks its digest, then
passes true native stdout/stderr without synthetic display output. It passes
only supported non-fullscreen host environment settings, preserves user-defined
terminal/color preferences, and lets the canonical adapter use `--no-clipboard`.

Python itself cannot prohibit arbitrary users from directly executing an
untrusted external PYZ. The guarantee applies to the documented verified
native entrypoint and native artifact pipeline, not arbitrary shell commands.

## Boundaries and acceptance

M2 provides **preflight**, not M3 automatic adoption or M4 approved remote
operations. A preflight receipt explicitly reports mutation permission false,
remote approval false and CI qualification false. Keep committed source blobs
and the negative tests in `tests/test_native_runtime_preflight_m2.py` updated
alongside future changes; do not delete v1/v2 compatibility or security gates.

Required acceptance consists of a real native Devtool source-bound check,
M2 tests plus affected existing tests, a successful committed native overlay
transaction, and exact-PR-HEAD hosted launcher workflow success. Device,
release, full-repository 24-shard regression and automatic M3/M4 coordination
are separate from this milestone.
