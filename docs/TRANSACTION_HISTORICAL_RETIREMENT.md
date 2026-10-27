# Historical transaction marker retirement — NEXT-01

For *superseded*, **preflight-only** `rollback-failed` journals after Git HEAD
advances, use the new strict, read-only inspection first:

```sh
devtool -r "$PWD" transaction inspect FULL_TRANSACTION_ID --json
devtool -r "$PWD" transaction retire FULL_TRANSACTION_ID --preserve-current --dry-run --json
```

If and only if `eligible=true`, independently inspect the exact repository,
old/new SHA, affected paths and backups. Explicit opt-in:

```sh
devtool -r "$PWD" transaction retire FULL_TRANSACTION_ID --preserve-current --yes --json
```

The operation moves only the exact matched active marker file(s) under the
transaction's private `historical-retirement` escrow, recording SHA-256,
current/old HEAD, Git index digest, and journal digest. It does **not** change
the journal, backups, index, worktree, HEAD, worktree isolation, or validation.
The operation is neither a rollback nor a clean release qualification. The
write-ahead escrow permits diagnosis after an interruption; do not manually
delete private receipts. For reversible restoration (only while the exact
current HEAD/index and journal remain unchanged):

```sh
devtool -r "$PWD" transaction unretire FULL_TRANSACTION_ID --yes --json
```

Anything live, dirty, mixed-history, already restoring, missing a required
backup, or containing untrusted symlinks is *blocked*. Use existing
`devtool overlay recovery-plan` to inspect ordinary rollback-retry cases.
This command does not call `abandon_transaction()`, which can integrate an
isolated worktree and is intentionally a different operation.

## Archive and startup notes

`apply-overlay` normally removes its original ZIP/sidecars after a
successful apply; use **`--keep-artifact`** to retain the source when rerunning
or auditing it. Failed applications preserve source evidence by default.
When normal Devtool startup fails before the CLI can parse options, use the
dependency-light `python -m devtool.bootstrap_doctor --format json`.
