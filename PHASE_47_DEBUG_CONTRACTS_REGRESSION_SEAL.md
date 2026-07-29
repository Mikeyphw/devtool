# Phase 47 - Debug contracts and regression seal

Phase 47 seals the Android debugging system added in Phases 34-46 with source-level regression contracts.

## Sealed areas

- Debug recorder schema and durable event persistence.
- Run command trace checkpoints from UI request to final result.
- Termux wrapper sentinels that do not corrupt command output.
- Authenticated bridge roundtrip probe.
- Repo Manager Doctor path/executor classification.
- Root/chroot executor planner fallbacks from `zsh` to `bash`/`sh` and from `runuser` to `su`/direct root.
- Repository path resolver and metadata-only repair behavior.
- Git operation trace preflight states before Git starts.
- Repo queue stale/orphan recovery actions.
- Debug Workbench UI as the central cockpit.
- Guided fixes with evidence, meaning and safe next actions.
- Support Bundle v2 redaction and bounded debug evidence.
- Debug notifications with safe actions only.

## Explicit regressions guarded

- Repo doctor must classify path missing in the selected namespace.
- Repo doctor must classify `runuser` failure before Git starts.
- Executor planner must not hard-require `zsh` or `runuser`.
- Git operation trace must distinguish preflight failure from Git command failure.
- Wrapper sentinels must emit checkpoints through authenticated callbacks instead of writing trace noise into Git stdout/stderr.
- Bridge probe must require authenticated callback evidence.
- Support Bundle v2 must include redacted repo/run/bridge/debug sections.
- Debug notifications and guided fixes must never expose destructive Git/file actions.

See `docs/android-debug-regression-seal.json` for the machine-readable contract inventory.
