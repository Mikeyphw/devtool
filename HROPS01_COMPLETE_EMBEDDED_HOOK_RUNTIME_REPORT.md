# HROPS01 — Complete Embedded Hook Runtime

Date: 2026-09-11

## Scope delivered

HROPS01 completes the generic artifact-hook runtime promised by the consolidated Devtool roadmap. It builds on the already-applied JSON finalize-hook foundation and converges preflight, validation, and finalize execution into one production subsystem.

### Runtime convergence

`devtool.hooks.EmbeddedHookEngine` now owns embedded script extraction/execution, SHA-256 verification, runner selection, Python project-environment launch, timeouts, live/log output, private artifact copies, JSON payload transport, structured results, capability reporting, repository mutation boundaries, and hook event metadata. `devtool.artifact_validation` contains lifecycle adapters rather than its own process runner.

### Protocol compatibility

- Preflight default: `legacy-env`; optional `json-v1`.
- Validator default: `legacy-env`; optional `json-v1`.
- Finalize: `json-v1`.
- Existing `DEVTOOL_PREFLIGHT_*` variables remain available.
- Existing initial-finalizer JSON-v1 top-level aliases remain available.

JSON-v1 is sent on stdin and mirrored to `DEVTOOL_HOOK_PAYLOAD_PATH`. The hook receives only the protected artifact-copy path, never the original artifact source path.

### Structured results

Every hook gets `DEVTOOL_HOOK_RESULT_PATH`. Optional schema-v1 results publish `status`, `summary`, `warnings`, `annotations`, and `evidence`. Structured failure/malformed output has deterministic failure semantics independent from normal human stdout/stderr.

### Capabilities and writes

Supported declarations are `repo_read`, `repo_write`, `artifact_read`, `artifact_private_copy`, `subprocess`, `network`, `environment`, and `secrets`. Effective reports explicitly distinguish enforcement from declaration. Repository write enforcement uses declared `write_paths`, dirty-file fingerprints, symlink-aware state, and non-Git snapshot fallback.

Validator mutation enforcement is opt-in through `validation.write_paths` for backward compatibility with existing build-producing validators. Preflight and finalization retain fail-closed write boundaries. `after_commit` is observational and restores unauthorized repository mutations where safely possible.

### Lifecycle

Canonical hook boundaries are:

```text
preflight.before_extract
apply/extract
preflight.after_extract
finalize.before_validation
validation.validate
finalize.after_validation
finalize.before_commit
Git commit
finalize.after_commit (observational)
```

### Inspection and history

`inspect-artifact` publishes a `hooks` section covering configured phases, capabilities, write paths, script path, runner, protocol, SHA-256, and timeout. Apply history records ordered hook events with timing, result, capability, mutation, and structured evidence fields.

## Compatibility guarantees

HROPS01 deliberately preserves old shell preflight artifacts, existing environment variables, legacy validator protocol, finalize JSON-v1 aliases, supported runners, Python validator environment behavior, and existing transaction/rollback semantics.

## Remaining intentionally future work

OS-level no-network/read-only/subprocess isolation is not faked. Capability reports identify what can actually be enforced on the current host. Stronger host sandboxing can be added later without changing the JSON-v1 lifecycle contract.

## Validation target

The overlay carries dedicated HROPS01 tests plus the existing artifact/preflight/finalize/inspection/CLI suites. The final packaged overlay should be accepted only after the complete repository pytest inventory passes and `devtool final-seal --strict` remains clean.
