# Artifact hook runtime audit

Date: 2026-09-11  
Baseline: post-FS03 plus `devtool_artifact_preflight_finalize_json_hooks_v1`

## Audit conclusion

The original artifact-script architecture had strong transaction and checksum behavior but three different lifecycle concepts—preflight, validator, and finalize—were still coupled to one monolithic runner and did not expose one complete public contract. HROPS01 finishes that convergence.

All artifact-controlled scripts now execute through `devtool.hooks.EmbeddedHookEngine`. Preflight, validator, and finalize are thin lifecycle adapters. They share script integrity verification, private artifact copies, timeout/logging behavior, JSON payload transport, structured result handling, capability reporting, repository mutation detection, and history evidence.

## Completed improvements

1. **JSON-v1 preflight.** `preflight.script.protocol = "json-v1"` uses the same versioned stdin/file payload as the other hooks. Existing environment-only preflight artifacts remain valid indefinitely; `DEVTOOL_PREFLIGHT_*` variables are preserved.
2. **Generic embedded hook engine.** Preflight, validator, and finalize no longer own independent subprocess runners.
3. **Declared capabilities.** Hooks may declare `repo_read`, `repo_write`, `artifact_read`, `artifact_private_copy`, `subprocess`, `network`, `environment`, and `secrets`. The runtime reports whether enforcement is `enforced`, `partially_enforced`, `declared_only`, or `unavailable`; it never claims an OS sandbox that is not present.
4. **Structured results.** Every hook receives `DEVTOOL_HOOK_RESULT_PATH`. A schema-v1 result can publish status, summary, warnings, annotations, and evidence without turning stdout into a machine protocol.
5. **History evidence.** Apply history records ordered hook events including kind/phase, protocol, script SHA-256, start/end/duration, return code, status, capabilities, declared/detected writes, restoration state, warnings/annotations, and structured evidence.
6. **Artifact inspection.** `inspect-artifact` exposes hook phases, protocol, runner, timeout, checksum, capabilities, and write scope in a dedicated `hooks` object.
7. **Unified integrity checks.** Overlay and bundle manifests validate hook runner/protocol/checksum/path/timeout/capabilities through the same manifest policy.
8. **Extended lifecycle.** Preflight can run at both extraction boundaries; finalize supports `before_validation`, `after_validation`, `before_commit`, and observational `after_commit`.

## Security and mutation model

Embedded scripts receive a private copy through `DEVTOOL_ARTIFACT_PATH`; the JSON payload does not disclose the original artifact filesystem path. Scripts still receive the artifact name, size, digest, and private-copy path needed for inspection.

Repository writes are bounded by explicit `write_paths` whenever the lifecycle enforces a write boundary. Devtool detects new, modified, deleted, and symlinked paths, including changes to files that were already dirty before hook execution. Git repositories use Git state plus content fingerprints; non-Git repositories use file-state snapshots. Absolute paths, parent traversal, and `.git` mutation declarations remain forbidden.

`after_commit` is observational. Repository mutations are not allowed even if the same finalizer has declared writes for an earlier phase. If an after-commit script changes the tree, Devtool restores the pre-hook state where safely possible and reports the violation without silently rewriting the completed commit.

Validators retain backward compatibility: historically they may build and create output directories, so validator writes are constrained only when `validation.write_paths` is explicitly declared. New artifacts should declare write scope when deterministic validation outputs are expected.

## JSON-v1 contract

The common envelope contains structured `hook`, `run`, `artifact`, `repository`, `target_context`, `validation`, `commit`, `execution`, `capabilities`, `declared_writes`, and `context` objects. Compatibility aliases such as `event`, `phase`, `run_id`, `target`, `solution`, and `write_paths` remain available for the initial finalize JSON-v1 contract.

The payload is delivered on stdin and mirrored to the owner-only path in `DEVTOOL_HOOK_PAYLOAD_PATH`. Structured output is written to `DEVTOOL_HOOK_RESULT_PATH`.

## Remaining future hardening

The remaining work is host isolation, not another hook API redesign. Future runtimes may enforce requested no-network, read-only repository, subprocess restrictions, or secret brokering when the host exposes trustworthy primitives. Until then, capability reports deliberately distinguish declaration from enforcement.

A future schema version may also add richer typed generated-artifact references to structured hook results. Such an extension should remain additive to JSON-v1.

## Usage guidance

Use `preflight` for prerequisites and transformations specifically tied to extraction, normal validation for build/test/lint work, `finalize.before_validation` / `after_validation` for deterministic validation-boundary checks, `finalize.before_commit` for last-mile commit/release assertions, and `finalize.after_commit` only for observational reporting.
