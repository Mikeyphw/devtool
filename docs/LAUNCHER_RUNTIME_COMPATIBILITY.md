# Devtool Launcher v3 — source-attested forward runtime compatibility

## Contract

New `.pyz` packages built with launcher v3.1+ record both:

1. The **immutable build runtime identity** (`Mikeyphw/devtool` origin,
   original 40-character HEAD/tree, launcher module blob and canonical CLI
   schema blob). These values are never overwritten, guessed or silently
   repinned.
2. An explicit **runtime ABI requirement** in `manifest.json`:
   `{"runtime_compatibility":{"minimum":"3.1.0","major":3}}`.

The runtime is allowed when its *committed literal*
`LAUNCHER_CONTRACT_VERSION` is greater than or equal to the package minimum
**within major 3 only**. Examples for a package built at v3.1.0:

| Runtime | Permitted | Reason |
|---|---|---|
| `3.1.0` | yes | exact compatible version |
| `3.1.1` | yes | maintenance update |
| `3.2.0` | yes | forward minor within the same ABI major |
| `3.9.7` | yes | higher compatible 3.x |
| `3.0.9` | no | below package minimum |
| `4.0.0` | no | different major ABI |
| `2.99.0` | no | different major ABI |
| `3.2.0-rc1` | no | prerelease not an accepted stable runtime version |

Major is an ABI promise, not a claim about the package's project release
number. A future *backward-incompatible* launcher must use major v4 and
cannot claim a compatible v3 version to bypass these checks.

## Security remains source-bound

Runtime compatibility is **not** source compatibility and **not** permission
for arbitrary Python execution. Before importing the runtime module, the
bootstrap verifies:

- The runtime is a real Git worktree with exactly `Mikeyphw/devtool` origin.
- Its actual current HEAD and tree are recorded; detached trusted runtime
  worktrees are supported.
- The committed launcher module and CLI schema Git blobs exactly equal their
  working-file content.
- No unrelated uncommitted/untracked changes are present; only existing
  validated Devtool LIFE cleanup receipts can remain.
- A single, literal version declaration and v3 launcher schema marker exist
  in that committed module, and the version is within the immutable ABI range.
- After the module loads, the canonical CLI schema is inspected for the
  required native command paths; the real native commands remain independently
  checked and executed by Devtool.

When the target and runtime refer to the same checkout, the runner freezes a
**separate exact current SHA** in an independent detached runtime worktree
before any native overlay changes. This is not an auto-rebase/branch mutation.
A requested runtime with an incompatible version refuses and does not
silently fall back to an old pinned commit. No network access or version
upgrade happens during compatibility selection.

The **target** repository/branch/source SHA/tree/preimage blobs and *every*
immutable payload byte continue to be exact-pinned and verified. The current
runtime is attested separately (SHA/tree/module/schema), never substituted
into the package's saved build provenance.

Legacy `.pyz` packages with no `runtime_compatibility` remain **exact runtime
SHA/tree/module/schema pinned**. The new feature does not reinterpret or
silently upgrade their trust model. To use version compatibility, rebuild a
**new package from reviewed current source**, rather than modifying an
existing immutable one.

## Operator acceptance

For every native delivery, record repository, target branch, source HEAD/tree,
current launcher version, runtime HEAD/tree/module/schema, payload inventory,
new commit, durable session, PR and *actual* hosted CI. A successful GitHub
check is not equivalent to a qualified Devtool receipt, and device/release
validation is separate.

Supported settings keep Textual/full-screen hosts disabled:

```sh
DEVTOOL_EXECUTION_PRESENTATION=plain
DEVTOOL_JOB_UI=plain
DEVTOOL_TEXTUAL_APPLY_HOST=0
DEVTOOL_TEXTUAL_EXECUTION_HOST=0
```

Rich monitor rendering is terminal-only. Redirected output, `NO_COLOR`,
`CLICOLOR=0`, `TERM=dumb`, machine JSON, full real native stdout/stderr,
no automatic clipboard writes and immutable session recovery remain intact.

## Canonical CLI ABI recovery (UX-RICH01)

The authoritative CLI parser defines `validate-overlay` as a flat top-level command. The earlier launcher mistakenly checked `('validate', 'overlay')`, rejecting valid compatible runtimes. The native runtime ABI inventory, read-only `--help` probe and native overlay validation invocation now all use `validate-overlay`. The compiled parser projection is checked in regression tests, alongside negative tests for genuinely missing commands. This does not waive immutable package source/HEAD/tree/blobs, CI policy or same-major 3.x compatibility requirements. Existing packages retain their original pins and are never rewritten.
