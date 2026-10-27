# NEXT-05 — Source-locked multi-repository adoption

**Implementation date:** 2026-10-21. **Authority:** source implementation and disposable Git tests only. No multi-repository Git atomicity, live adoption, real consumer qualification, native Termux acceptance or release authority.

## Operator interface

Use the canonical CLI:

```sh
devtool --no-hud adopt-batch plan /absolute/adoption.json --state-dir /safe/external/state --json
devtool --no-hud adopt-batch apply /absolute/adoption.json --state-dir /safe/external/state --yes --json
devtool --no-hud adopt-batch status /absolute/adoption.json --state-dir /safe/external/state --json
devtool --no-hud adopt-batch resume /absolute/adoption.json --state-dir /safe/external/state --yes --json
```

The same operations are available through `python -m devtool.adoption_batch`. The `plan` and `status` paths do not mutate any repository or create a journal. `apply`/`resume` require `--yes`; a manifest cannot silently approve them.

## Reviewed input manifest (illustrative; replace all values)

```json
{
  "schema": "devtool.multi-repo-adoption.v1",
  "repositories": [
    {
      "id": "project-a",
      "path": "/absolute/path/to/project-a",
      "source_head": "<full reviewed 40-character Git commit SHA>",
      "source_tree": "<full reviewed 40-character Git tree SHA>",
      "branch": "main",
      "remote": "origin",
      "remote_url": "https://example.org/owner/project-a.git",
      "overlay": "/absolute/path/to/project-a-source-bound.zip",
      "overlay_sha256": "<64-character reviewed overlay SHA-256>",
      "wrapper": true,
      "migrate_config": true
    }
  ]
}
```

Do not fill Git identities from a guessed remote, or use an overlay for a different revision. Every referenced ZIP must be a valid source-bound Devtool overlay with a file inventory. Source links and artifact SHA-256 are verified before changes. Git remote URL **equality is a local identity check, not independently authenticated GitHub ownership**.

## Guarantees and state model

The coordinator reads all repositories **before changing the first checkout**: branch, remote, HEAD/tree, overlay integrity, tracked preimages, dirty/staged state, transaction markers, wrapper conflicts and supported legacy migration. A generated composite overlay is checked by native Devtool for every `FRESH` repository before the prepare barrier opens. No automatic rebase or force overwrite is authorized.

- `FRESH`: source and reviewed preimages match; existing Devtool overlay transaction applies one composite manifest and commits its allowlisted files.
- `APPLIED_UNCOMMITTED`: only the declared file outputs match, source HEAD is unchanged, and no unrelated edits or staged index changes exist; finish with one allowlisted Git commit.
- `COMMITTED`: the journal records the exact committed HEAD/tree, branch/remote remain pinned and the checkout is still at that commit; skip on resume.
- `PARTIAL`: some files differ or the Nth repository was interrupted. Earlier repository commits are retained and reported honestly.
- `DRIFTED`: overlay source equivalence is unavailable or changed; do not overwrite.
- `BLOCKED`: unsafe identity, incomplete journal, transaction marker, unmanaged launcher, symlink, dirty checkout or unknown source state; requires manual correction and fresh plan.

The journal lives **outside all repositories**, under `~/.local/state/devtool/adoption-batches` by default, using a manifest SHA-256 identity, a process lock, atomic write/fsync and exact per-repository commit SHA/tree. If a crash occurs after a commit but before its receipt write, recovery requires a one-parent commit from the original source HEAD, exact expected commit subject, no undeclared changed paths, clean checkout and all prepared output hashes; otherwise it blocks. No prior commit is reset, amended or pushed.

`wrapper=true` stages `devtoolw`, `devtoolw.cmd`, and `.devtool-wrapper.lock` in the **same** owned composite transaction. The POSIX launcher is checked for executable mode. Unmanaged or symlinked launchers block. `migrate_config=true` uses the existing supported `config_migration.migrate_config_text` transformation while retaining unsupported tables/keys; ambiguous known migrations fail closed. It cannot automatically merge a concurrent `.devtool.toml` overlay edit; that combination is explicitly blocked. Exact historical source bytes remain available to the ordinary per-repository Devtool transaction journal.

The coordinator does **not** atomically commit multiple Git repositories. A successful commit in repository A cannot be rolled back in response to a failure in repository B. Nothing here claims independent remote identity, signed package authenticity, CI, device, security or release qualification.

## Testing and known boundaries

Disposable negative fixtures cover remote/branch/source drift, renamed checkout, dirty worktree, existing generated outputs, previously applied outputs, optional config/wrapper staging, interrupted prepare versus interrupted Nth apply, idempotent resume, ZIP tampering and symlinks. No real six-repository archives or current consumer HEADs were provided for end-to-end acceptance; that remains a separate gate.

**Not in this batch:** generalized legacy config arbitrary-extension migration, automatic partial patch reconstruction, a project discovery wizard, native automatic rebase, multi-repo atomic rollback, cross-repository extraction/pruning (NEXT-06), or an independent external-authority acceptance certificate. Existing `overlay rebase` and `transaction inspect/retire/unretire` remain separate safety owners.
