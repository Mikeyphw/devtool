# Overlay 5 of 15 — Transactions, overlays, archives and rollback

## Scope

This overlay closes the remaining S05 audit findings assigned to the transaction, overlay, archive and rollback layer.

Assigned findings closed:

- `S05-01` — Overlay writes through symlink but rollback restores only symlink path and leaves real target mutated.
- `S05-02` — Preflight hook write paths omitted from backup if they overlap unchanged payload.
- `S05-03` — Rollback clobbers user edits after successful apply.
- `S05-04` — Snapshot overlays can delete `.devtool` metadata while preview/rollback exclude it.
- `S05-05` — Rollback leaves parent directories created for added nested files.
- `S05-06` — Empty directory additions are invisible to preview/transaction/rollback.
- `S05-07` — Unsafe `.devtool-delete` traversal passes validation.
- `S05-08` — Extraction drops executable bits and file modes.
- `S05-09` — Replacing a directory with a file passes validation/preview but fails at apply time.
- `S05-10` — Duplicate normalized archive paths are not rejected.
- `S05-11` — Malformed legacy active transaction metadata crashes recovery.
- `S05-R2-02` — Payload plus `.devtool-delete` same-path conflicts apply as success.
- `S05-R2-03` — Internal archive file/child collisions are accepted and rollback leaves residue.
- `S05-R2-04` — Nested files named `.devtool-delete` are treated as destructive manifests.
- `S05-R2-05` — JSON delete manifests coerce non-string values into paths.
- `S05-R2-06` — Bundle schema v2 integrity requirements can be bypassed with `implicit = "false"`.
- `S05-R2-07` — Malformed bundle dependencies are silently ignored when written as strings.

## Implementation summary

### Archive and manifest semantics

- Archive member names are now normalized through one POSIX-safe path routine before validation, preview or extraction.
- Absolute paths, traversal, backslashes, NUL bytes, Windows drive-style prefixes and `.git` root paths fail closed.
- Duplicate normalized spellings such as `./a.txt` and `a.txt` are rejected.
- File/child collisions such as `a` plus `a/b.txt` are rejected.
- ZIP symlink members and tar links/devices/unsupported members are rejected.
- ZIP and tar file modes are preserved during extraction.
- Only root-level `.devtool-delete` is a destructive delete manifest; nested files named `.devtool-delete` are normal payload.
- JSON delete manifests must be lists of strings; invalid entries are reported instead of coerced.
- Payload/delete same-path conflicts are rejected before mutation.
- Payload files that would replace existing directories are rejected during artifact validation.

### Transaction and rollback semantics

- Extraction now replaces the final symlink/file path without following a symlink target outside the transactional path.
- Transaction entries record newly created parent directories so rollback can remove empty parent directories created solely for the overlay.
- Empty directory additions appear in preview as `add-dir` and participate in transaction planning and rollback.
- Successful apply now checkpoints post-apply path identities.
- Rollback refuses to overwrite live files that changed after Devtool successfully applied the overlay, preventing user-edit clobber.
- Malformed legacy active transaction metadata is treated as stale/corrupt state and removed instead of crashing transaction discovery.

### Snapshot and hook semantics

- Snapshot sync excludes `.devtool/**` metadata from deletion regardless of whether preview omitted it.
- Declared preflight/validation/finalize hook write paths are always represented in preview. If a declared write path overlaps unchanged payload, the change is upgraded to the hook-write kind so the transaction backs it up.
- Hook write paths targeting `.git/**` retain a specific validation error rather than falling through to a generic unsafe-path message.

### Bundle semantics

- Schema v2 bundle integrity enforcement now requires a real boolean `implicit`; string values such as `"false"` no longer bypass sha256/size checks.
- Bundle `after` and `requires` dependencies must be lists of strings; malformed strings or non-string entries fail validation.

## Validation performed

Focused transaction/archive overlay gate:

```text
120 passed
```

Regression sweep over previous remediation seams plus S05 suites:

```text
189 passed
```

Execution/scheduler compatibility sweep:

```text
210 passed
```

A full repository final qualification is intentionally deferred to Overlay 15 of 15.
