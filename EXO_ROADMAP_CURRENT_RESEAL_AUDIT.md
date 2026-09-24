# EXO Roadmap Current Re-seal Audit

Status: **PASS after remediation; no remaining material EXO roadmap gaps found in the current fixed-point pass.**

## Authority and scope

This audit treats the current repository snapshot as authoritative and re-checks the full EXO execution-convergence promise set: EXO-01 through EXO-11, the post-seal promise-remediation/P0 contracts, and the later WRAP, target-local-job, FRX, SSH/OCI, desktop/TUI, Android-HUD integrations that consume EXO state.

The audit loop was repeated after every material fix. A clean test suite alone was not accepted as sufficient evidence; producer/consumer contracts, embedded-resource digests, persistence schema compatibility, and downstream federation/provider consumers were inspected separately.

## Roadmap promise matrix

| Roadmap area | Current authoritative implementation/evidence | Result |
| --- | --- | --- |
| EXO-01 execution identity | stable run/node/process/repository identity contracts and tests | PASS |
| EXO-02 typed event protocol | versioned event kinds, unknown-future-event compatibility, serialized payload contracts | PASS |
| EXO-03 authoritative store | reducer/store ownership, graph sealing, sequence rules, durable snapshots | PASS |
| EXO-04 truthful accounting | direct/recursive/leaf accounting, measured-vs-resolution progress, blocked state | PASS |
| EXO-05 runner adapters | Gradle/npm/.NET/generic child events; Gradle cache/up-to-date/no-source distinction re-sealed | PASS |
| EXO-06 diagnostics/evidence | process, diagnostics, logs and artifact ownership/deduplication | PASS |
| EXO-07 persistence/IPC/history | journal replay/recovery/checkpoints, IPC/Tasker/history canonical projections | PASS |
| EXO-08 terminal/TUI | canonical run/node truth, worker shortcut/state and terminal projections | PASS |
| EXO-09 desktop/GTK | shared canonical execution state and desktop control-center/HUD truth | PASS |
| EXO-10 Android | typed execution graph transport, bounded refs, dependency/blocked/progress fidelity | PASS |
| EXO-11 final convergence | cross-surface seal, stale-legacy rejection, stable IDs, compatibility | PASS |
| post-seal remediation/P0 | durable Android graph, bounded evidence, synthetic transition provenance, CLI/workflow terminal finalization | PASS |
| WRAP/TLJ consumers | canonical EXO provenance, affinity, dispatch and final seals | PASS |
| FRX consumers | parent/child lineage, federation execution gates, remote identity and ownership boundaries | PASS |
| SSH/OCI consumers | provider repository/execution identity, embedded telemetry parity/digest pinning | PASS |

## Audit loop findings and remediation

### Gap 1 — stale execution-snapshot acceptance contract

The historical promise-remediation test still asserted snapshot schema `5`, while later additive WRAP and FRX work legitimately advanced the canonical schema to `7`. The runtime also embedded `7` directly in `ExecutionSnapshot.from_state()`, which made future acceptance drift too easy.

Remediation:

- introduced the exported `EXECUTION_SNAPSHOT_SCHEMA_VERSION = 7` authority;
- made snapshot construction use that constant;
- changed the historical v5 promise check to assert `>= 5` plus equality with the current authority;
- re-sealed documentation to distinguish historical EXO-11 schema v4, post-seal schema v5, WRAP v6, and current FRX v7.

### Gap 2 — Gradle work-avoidance outcome flattening

The structured Gradle producer evaluated generic `state.skipped` before the more specific `upToDate` and `noSource` states, and it had no `FROM-CACHE` outcome. That contradicted the EXO-05 promise that cached, up-to-date and no-source outcomes remain distinguishable.

Remediation:

- producer now resolves `FROM-CACHE` from `skipMessage` first;
- preserves `no-source` and `up-to-date` before generic `skipped`;
- artifact reuse classification now includes `from-cache`;
- added regression coverage at producer, canonical reducer metadata, plain Gradle telemetry, and artifact classification layers;
- kept the canonical and OCI-embedded Gradle scripts byte-identical.

### Gap 3 — OCI/SSH embedded-runtime digest coupling

Correcting the Gradle producer changed its SHA-256, revealing that OCI/SSH runtime contracts pin the embedded telemetry bytes. Without updating all consumers, the canonical OCI image contract would reject the corrected runtime.

Remediation:

- updated the OCI image manifest and validator digest;
- updated OCI image/execution and SSH/tmux consumers;
- updated repository-execution contract fixtures;
- added a current re-seal regression that verifies every digest consumer agrees with the canonical telemetry bytes.

Current Gradle telemetry SHA-256:

`621ce78be0c05cd5e5e43668461795ae0ed81de89acf40e0e8843d52248995a4`

### Gap 4 — artifact lifecycle subprocesses bypassed authoritative EXO

Embedded preflight, validation, and finalize lifecycle scripts emitted Tasker/Rich
phase callbacks, but the hook engine launched their subprocesses directly. That made
them visible to compatibility progress surfaces without making them authoritative EXO
nodes/processes. The UI could therefore show coarse lifecycle progress while EXO
history, persistence, diagnostics and replay had no canonical process ownership.

Remediation:

- added `HookExecutionAdapter` as the canonical lifecycle execution bridge;
- every embedded preflight/validator/finalize phase is an `artifact-hook` EXO node;
- the spawned hook runner is a canonical EXO process using the shared runner adapter;
- hook failures publish exit code, failure kind, diagnostic IDs and log evidence;
- repeated lifecycle attempts receive distinct `hook_attempt` identities;
- durable `ExecutionProjectionSubscriber` receives the hook events, proving they feed
  IPC/HUD/desktop/Android execution projections instead of Tasker-only state;
- Tasker/Rich phase callbacks remain compatibility projections.

### Gap 5 — stale workers-shortcut acceptance contradicted the EXO remediation

The repository already contained the fixed centralized `w -> workers` TUI shortcut,
but an older UI01 contract still asserted the pre-remediation three-key map. Because
the handoff explicitly records the missing-workers issue as an EXO promise-remediation
item, the stale test was part of the EXO acceptance drift.

Remediation: the UI01 contract now asserts the current centralized four-key map.

## Validation evidence

After the final remediation loop:

- full EXO + P0 + WRAP + target-local-job acceptance: **249 passed**;
- OCI/SSH provider/image focused suite: **79 passed**;
- broad execution/provider/UI slice: **403 passed**;
- FRX-01..12 plus current EXO re-seal: **109 passed**;
- current combined EXO/lifecycle/WRAP/TLJ/FRX/SSH/OCI/UI fixed-point selector: **672 passed**;
- artifact lifecycle EXO/projection focused contract: **4 passed**;
- hook/artifact/execution compatibility slice: **120 passed**;
- EXO/WRAP/TLJ/FRX acceptance slice: **338 passed**;
- touched-module compilation and repository `compileall`: PASS;
- embedded Gradle scripts are byte-identical and their SHA-256 matches every pinned consumer;
- stale hard-coded schema-5 and old embedded-Gradle-digest references: none remain in the audited EXO surfaces.

## Full-repository context

A repository-wide pytest run collected 3328 tests but exceeded the audit sandbox execution window around the first quarter of the suite. The sampled failures encountered there were reproduced unchanged against the pristine uploaded baseline (for example stale golden CLI help and a mocked `tasker_progress` signature), so they are pre-existing repository-wide debt rather than EXO roadmap regressions or effects of this re-seal. They are intentionally not mixed into this EXO remediation overlay.

## Final verdict

The audit loop reached a fixed point: the last pass found no additional material EXO roadmap gap after rechecking the original EXO contracts, the post-seal remediations, and all current major execution consumers. The current re-seal should therefore be treated as the executable compatibility authority for the evolved schema/runtime state.


## Current lifecycle convergence addendum

A later audit found that embedded artifact lifecycle scripts (preflight, validation,
finalize/before-commit/after-commit) still used Tasker/Rich phase callbacks as the
primary live-progress path while their subprocesses bypassed the authoritative EXO
node/process graph. That was not full EXO convergence.

The current re-seal routes every embedded hook through `HookExecutionAdapter`:
- the lifecycle phase is a canonical `artifact-hook` EXO node;
- the actual spawned script runner is a canonical EXO process;
- failures carry exit code, failure kind, diagnostics, and durable log evidence;
- retries receive distinct hook-attempt node identities;
- `ExecutionProjectionSubscriber` receives these events, so terminal/desktop/Android/IPC
  surfaces can render the same authoritative execution truth;
- Tasker/Rich phase callbacks remain compatibility projections, not lifecycle authority.

This addendum is enforced by `tests/test_exo_lifecycle_hook_convergence.py` and the
current re-seal contract.
