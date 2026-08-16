# Devtool OV-15 / OV-16 final seal implementation report

## Scope

This overlay implements the final combined remediation round:

- OV-15: installer and repository packaging hardening.
- OV-16: release evidence, CI qualification, and final seal invariants.

## Implemented changes

### OV-15 — Installer and repository packaging

- `install.sh` now writes installer state stamps through same-directory temporary
  files, `0600` permissions, file fsync, atomic rename, and directory fsync when
  Python is available. Interrupted app/module installs no longer leave partially
  written source-fingerprint stamps.
- `pack_repo.sh` now validates requested pack inputs as repository-relative
  paths and rejects absolute paths, parent traversal, double separators, and
  backslash separators.
- `pack_repo.sh` now produces deterministic archives using stable ordering,
  `SOURCE_DATE_EPOCH`, root owner/group metadata, and deterministic mode bits.
- `pack_repo.sh` now defaults to `.tar.zst`, supports explicit
  `--compression zstd|gzip`, writes archive SHA-256 files, and can emit a
  per-file SHA-256 manifest with `--manifest`.

### OV-16 — Release evidence, CI, and final seal

- `tools/build-release.py` now emits a schema v2 release manifest with the
  release versions, source tree SHA-256, artifact SHA-256, git head when
  available, and hash algorithm.
- Release artifact manifests and checksum files are written atomically and
  verified against the artifact bytes before publication.
- `devtool.release_qualification` now records initial/final Git HEAD and tracked
  index/worktree status, adds a `git-worktree-invariants` gate, and writes a
  content-hashed release qualification evidence report.
- `tools/release-qualify.py --final` now writes a write-once `.final.json`
  evidence report and prints the evidence SHA-256.
- `tools/make-release-candidate.sh` now requests final evidence output and writes
  `SHA256SUMS.all` through a temporary file before final replacement.
- The release qualification workflow now provisions Python, Java 17, Android SDK
  packages, an ephemeral CI release keystore, and uploads desktop, Android,
  device, and source evidence artifacts.
- Removed stale references to the deleted
  `tests/test_phase33_validation_release_seal.py` from release qualification
  paths so the release seal invokes only existing tests.

## Validation performed

Focused OV-15/OV-16 and regression suite:

```bash
python3 -m pytest -q \
  tests/test_ov15_ov16_release_final_seal.py \
  tests/test_ui_ux_slice5.py \
  tests/test_desktop_gtk_phase8_9_final.py \
  tests/test_desktop_gap_closure.py \
  tests/test_installer_ux.py \
  tests/test_desktop_gtk_phase5.py \
  tests/test_validation_reports_phase32.py
```

Result: `43 passed`.

Full deterministic Python split groups were run individually:

```bash
python3 tools/run-pytest-split.py --groups 4 --group 1 --group-timeout 300 --retries 0
python3 tools/run-pytest-split.py --groups 4 --group 2 --group-timeout 300 --retries 0
python3 tools/run-pytest-split.py --groups 4 --group 3 --group-timeout 60 --retries 0
python3 tools/run-pytest-split.py --groups 4 --group 4 --group-timeout 60 --retries 0
```

Results:

- Group 1: `377 passed, 8 subtests passed`.
- Group 2: `413 passed, 35 subtests passed`.
- Group 3: `376 passed`.
- Group 4: `414 passed, 14 subtests passed`.

Additional checks:

```bash
python3 -m compileall -q devtool tools tests
python3 tools/build-release.py --root . --out-dir /mnt/data/build_release_test --verify-reproducible
```

Results: compile succeeded; deterministic source release generation succeeded.

A single end-to-end `tools/release-qualify.py --core-only --final` run was also
attempted in the sandbox. It exceeded the tool-call timeout after completing the
first invoked split group; the same split groups passed individually as recorded
above.


## Post-final diagnostic truthfulness correction

A post-apply log audit found two reporting-layer gaps after the final overlay:

- Raw command warnings could be copied in the success summary while the displayed diagnostic count still said `0 warnings`.
- Successful commands could print diagnostic-looking fixture/demo text, such as xUnit, lint, Kotlin, Gradle, Python, or generic error examples, and Devtool could promote that captured stdout into a false command failure even though the process exited `0`.

The corrective seal now counts copied raw warning details in the success diagnostic total and applies a runner-agnostic success-boundary rule: command exit status is authoritative for generic successful commands, and captured stdout diagnostics are non-fatal unless the command explicitly sets `trust_stdout_diagnostics` / `fail_on_stdout_diagnostics`, or the command is a known dotnet/vstest boundary where stdout summaries remain authoritative after hang recovery. Structured report parsers and artifact validators remain authoritative when explicitly configured.

## Post-final reporting truthfulness correction v5

The copied summary now carries the complete deduplicated diagnostic detail set that Devtool knows about instead of only adjusting counts. Structured warnings are combined with raw command warnings, and failed-run summaries combine structured error issues with concrete raw failure lines from the failed command output. The failure summary no longer caps the `Errors:` section at twelve entries, so every unique copied error/warning detail is present in the clipboard payload.

Failure summaries now include an explicit `Failure reason:` section before the detailed error list. The reason is derived from the failed operation result, its exit code/failure kind, and the first structured error when available; if structured issues are missing, Devtool falls back to the failed command boundary or the collected raw error detail. This keeps copied failures pointed at the actual failing action instead of merely saying that a validation phase failed.
