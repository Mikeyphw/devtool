# Artifact preflight and finalization audit

Date: 2026-09-11
Baseline: post-FS03 Devtool snapshot at commit `9072b0b`

## Existing preflight strengths

- Runs inside the active apply transaction and supports `before_extract` / `after_extract` phases.
- Embedded hook bytes are checksum-verified and executed from disposable storage.
- `DEVTOOL_ARTIFACT_PATH` points at a private copy, so the source archive is not handed to the hook for mutation.
- `preflight.write_paths` scopes intentional writes and integrates with preview/transaction/reservation behavior.
- Source/payload checksum boundaries detect undeclared transformations and failures roll back.
- Timeouts, logs, live output, and Python project-environment execution are shared with the validator runner.

## Gaps found

1. Hook context is primarily environment-variable based. It works, but adding fields creates namespace sprawl and makes typed Python hooks awkward.
2. There was no artifact-owned lifecycle boundary after extraction but before the validator or commit.
3. A last-mile script implemented outside Devtool would not automatically inherit artifact checksum, timeout, transaction, logging, and failure semantics.
4. Preflight has phase-specific integrity checks, but there was no reusable JSON hook contract that future phases could share.
5. Final commit-time checks could only be approximated by the validator, even when they logically depend on successful validation.

## Implemented design

A separate top-level `finalize` policy keeps extraction preflight semantics unchanged and adds two explicit post-apply phases:

- `before_validation`: executes directly before the embedded artifact validator.
- `before_commit`: executes only when an automatic artifact commit is actually requested and validation/apply results are clean.

The hook uses `protocol: json-v1`. Devtool sends the payload over stdin and mirrors it to `DEVTOOL_HOOK_PAYLOAD_PATH`. Exit code 0 passes; nonzero/timeout blocks progression.

The JSON payload carries:

- schema/event/phase/run identity;
- repository, target, and solution identity;
- artifact path/name/SHA-256 (the path is a private copy inside the hook environment);
- validation requested/status;
- commit requested/message/path scope;
- declared repository write paths.

`finalize.write_paths` is mandatory for mutations. The runner fingerprints pre-existing dirty/untracked Git paths and all newly dirty paths, including content hashes, so an already-modified file cannot be changed invisibly. On non-Git repositories it falls back to a full file-state snapshot. `.git` targets, parent traversal, and absolute write declarations remain forbidden.

## Recommended next preflight improvements

These are intentionally not breaking changes to the current preflight contract:

1. **Add opt-in `preflight.script.protocol: json-v1`.** Reuse the final-hook payload transport while preserving all existing `DEVTOOL_PREFLIGHT_*` variables indefinitely for compatibility.
2. **Generalize the embedded hook engine.** Move common checksum/private-copy/timeout/stdin/logging logic behind one internal hook runtime used by preflight, finalize, and validator adapters.
3. **Add declared capabilities.** Optional `capabilities` such as `repo_read`, `repo_write`, `network`, and `subprocess` would make artifact intent reviewable even before OS-level sandboxing exists.
4. **Add structured result files.** Keep stdout human-readable, but optionally accept `DEVTOOL_HOOK_RESULT_PATH` JSON for warnings, annotations, generated paths, and machine-readable evidence.
5. **Capture hook evidence in artifact history.** Store phase, script SHA, payload schema, duration, declared writes, and result digest in the history record without storing sensitive environment values.
6. **Expose hook dry-run inspection.** `inspect-artifact` should show hook phases, protocol, timeout, write scope, and checksum in a dedicated machine-readable section.
7. **Unify bundle hook verification.** Bundle-level preflight/final hooks should use the same embedded-member checksum validator as overlays (the final-hook implementation extends this path for `finalize`).
8. **Optional execution policy.** Future manifests could request no-network or read-only-repo execution; enforcement should be capability-detected rather than pretending a sandbox exists where it does not.

## Usage recommendation

Prefer `preflight` for prerequisites that must happen around extraction, the normal validation plan for tests/build/lint, `finalize.before_validation` for lightweight deterministic normalization or assertions immediately before the embedded validator, and `finalize.before_commit` for release metadata or assertions that only make sense after validation has succeeded.
