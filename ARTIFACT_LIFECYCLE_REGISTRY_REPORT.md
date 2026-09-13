# Overlay 6 of 15 — Artifact lifecycle, verification and registry correctness

## Findings closed

This overlay closes every S06 finding in the final remediation roadmap:

- S06-01 — artifact verification errors now force a non-zero command result instead of being ignored by final exit handling.
- S06-02 — managed-output copy now copies outside artifacts even when another artifact is already in the managed output directory.
- S06-03 — cached Android fallback artifacts now use build-type-specific variants, so `assembleFreeRelease` cannot accept stale `freeDebug` output.
- S06-04 — APKs/AABs named `app.apk`/`app.aab` under release output directories now infer the full release classifier used by signer identity checks.
- S06-05 — malformed artifact registry `schema_version` is preserved as corrupt evidence and reset safely instead of crashing.
- S06-06 — registry pruning precomputes retained paths before deletion so an old record cannot delete a file still referenced by a newer retained record.
- S06-07 — registry pruning normalizes naive timestamps to UTC before cutoff comparisons.
- S06-08 — `.tar.zst`/`.tzst` verification fails closed unless a zstd frame and tar payload can be verified.
- S06-09 — string boolean `"false"` no longer enables artifact verification.
- S06-10 — registry artifact type detection preserves compound suffixes such as `tar.gz`.
- S06-R2-01 — artifact directory `recursive = "false"` is honored as non-recursive.
- S06-R2-02 — Gradle structured event `didWork = "false"` is not treated as produced output.
- S06-R2-03 — spec metadata `collect_artifacts = "false"` / `verify_artifacts = "false"` no longer enables collection or verification.
- S06-R2-04 — `copy_artifacts = "false"` disables managed-output copying.
- S06-R2-05 — `register_artifacts = "false"` disables registry writes.
- S06-R2-06 — multi-artifact copy with `collision = "replace"` cannot overwrite another artifact from the same batch.
- S06-R2-07 — `npm pack --json` artifact candidates are checked for recency before they are marked produced.
- S06-R2-08 — registry metadata stores recognized boolean fields as booleans, not truthy strings.

## Implementation notes

- Added a strict artifact boolean coercion helper for artifact pipeline metadata and runner options.
- Hardened artifact collection toggles, configured directory recursion, Gradle structured event production state, Go cached-output metadata, copy/register policies and package validity parsing.
- Reworked managed-output copying so existing managed artifacts do not short-circuit copying of distinct outside artifacts.
- Added intra-batch destination tracking so `collision=replace` may replace stale preexisting output but cannot replace another current artifact in the same run.
- Added fail-closed `.tar.zst` verification using zstd magic and external zstd/unzstd decompression before tar header iteration.
- Hardened registry loading, type inference, metadata boolean normalization and prune timestamp/path retention rules.
- Tightened Android artifact variant inference to include flavor/build-type directory components and removed flavor-only release-task fallback aliases.
- Made artifact verification failures set `artifact_verification_failed=true` and force return code `1` when the child process itself exits `0`.

## Focused validation

Executed with `PYTHONPATH=.` from the overlay worktree:

```text
13 passed — tests/test_rem06_artifact_lifecycle_registry.py
136 passed — artifact lifecycle, registry, Android artifact and integrity regression sweep
```

The full repository qualification remains intentionally deferred to Overlay 15 of 15.


## Folded Overlay 5 compatibility hotfix

This v2 overlay also includes the archive root-directory marker compatibility fix that was originally prepared as a separate Overlay 5 hotfix. Directory-only tar/zip members named `.`, `./`, or equivalent root markers are ignored during member listing, while empty non-directory names and root/absolute/traversal payload members still fail closed. This prevents harmless directory-root tar markers from blocking unrelated overlays without weakening unsafe payload validation.
