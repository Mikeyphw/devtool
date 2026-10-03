# REMED-R02A — Execution, Scheduler & RUN Truth (v2 fixed-point audit correction)

## Position

- Gate-window overlay: 3 of 8
- Full-plan position: 73 of 93
- Previous completed remediation state: REMED-R01A `7f7b6fd`, REMED-R01B `b5a8cce`, REM01 fixed-point hotfix `e16ef1d`
- Supersedes: `remed_r02a_execution_scheduler_run_truth_v1.zip`
- Next overlay: REMED-R02B — Validation & Test Truth, 74/93
- Remaining remediation implementation overlays before G3R after this overlay: 5
- Next major phase after remediation implementation: CORE-G3R at 79/93

## Audit result

The v1 overlay was not truthful enough to advance. It implemented useful scheduler/RUN production behavior, but it over-claimed several current EXEC findings:

- `EXEC-A01` and `EXEC-A02` are validation-state duplicate-authority findings. They require ValidationSession to reduce attempts, phase state, progress, heartbeat, control history, and durable state to contracts/verdict/control intent plus EXO/RUN references. R02A did not change that production authority, so these remain deferred to R02B.
- `EXEC-A06` requires production workflow, test, and validation consumers to share the same operational adoption path. R02A proves the workflow/direct `run_specs` boundary only, so this remains partial.
- `EXEC-A08` requires universal fresh/rerun/rerun-from override semantics. R02A does not finish that cross-ingress control model, so this remains deferred.
- `EXEC-A13` requires auditing and lowering all cache/reuse/resume paths into generic RER qualification. R02A covers normal workflow/CLI/direct `run_specs` scheduling, but not all validation/test/provider/domain cache paths, so this remains partial.

This v2 correction keeps the actual scheduler/RUN implementation, adds a missing negative guard for direct metadata bypass, and fixes the disposition/reporting so the overlay no longer declares unresolved findings complete.

## Canonical scope boundary

R02A now owns only the execution-scheduler/RUN substrate that is actually implemented and adopted here:

- `EXEC-A04` — normal scheduler obtains RER/RUN disposition before process spawn and honors RUN/REUSE/MATERIALIZE.
- `EXEC-A05` — reuse candidates are rebuilt from canonical EXO/RUN history rather than ad hoc projection caches.
- `EXEC-A07` — MATERIALIZE restores verified outputs and emits canonical EXO/RUN result without process execution.
- R02A contribution toward `EXEC-A06` and `EXEC-A13` — normal workflow/CLI/direct `run_specs` ingress uses this boundary, but validation/test/provider-specific consumers are not yet final-closed.

Deferred to R02B or later scopes:

- `EXEC-A01`, `EXEC-A02` — ValidationSession execution authority reduction.
- `EXEC-A06` remaining validation/test adoption.
- `EXEC-A08` universal fresh/rerun/rerun-from controls.
- `EXEC-A13` full cache/reuse/resume authority audit outside normal `run_specs`.
- Testcase/result taxonomy, Android instrumentation truth, ArtifactPair producer-consumer truth, remote/provider parity, cleanup/retention/GC barriers.

## Production adoption

### Canonical scheduler boundary

`devtool.execution_scheduler_truth` is the production-facing scheduler boundary. It derives decisions from `ReuseHistoryIndex.from_history_directory(...)`, which rebuilds reusable candidates from terminal EXO execution records under `history/<run_id>/execution.json`. It does not accept legacy projection files as reuse authority.

### Production ingress changed

- `run_specs(...)` computes scheduler decisions before task spawn.
- Workflow command specs receive `exec_scheduler_decision`, `exec_scheduler_disposition`, and `exec_scheduler_process_executed` metadata before `TaskStarted`.
- Authoritative `reuse` and `materialize` decisions complete the task with `transition_origin="rer_scheduler"` and do not create `ProcessStarted` events.
- `materialize` verifies `file:<path>#sha256=<digest>` refs before copying bytes.
- Failed, missing, corrupt, stale, or mismatched history fails closed to ordinary process execution.
- `ExecutionStore` persists terminal `ReusableNodeRecord.disposition` as `run`, `reuse`, or `materialize` instead of flattening every terminal proof into `run`.
- Normal CLI progress commands attach an `ExecutionProjectionSubscriber` and finalize RUN-V3 entries with execution snapshot/checkpoint/history attachment refs.

### v2 authority correction

The v1 WIP still accepted mutable `exec_scheduler_enabled=false` metadata as a scheduler bypass. v2 removes that bypass: public/spec metadata may force a non-reusable fingerprint that produces a canonical RUN decision, but it cannot disable the scheduler boundary itself.

## Evidence

Executed on the construction host:

```text
python3 -m py_compile devtool/execution_scheduler_truth.py devtool/runners.py devtool/cli.py devtool/core/execution_store.py
```

Focused + retained scheduler/RUN validation:

```text
python3 -m pytest -q --tb=short tests/test_remed_r02a_execution_scheduler_run_truth.py tests/test_exec_g1_reuse_evidence_gate.py tests/test_exec_x04_operational_adoption.py tests/test_exo_p0_cli_run_finalization.py tests/test_core_x03_run_store.py tests/test_core_x04_generated_surfaces.py
52 passed
```

Native-Termux artifact workflow is declared as:

```text
./devtoolw remed-r02a
```

The construction host is not Termux, so device-side Devtool validation remains authoritative for that wrapper workflow.

## Negative/adversarial cases

- Qualified REUSE skips process creation and records `process_executed=false`.
- Qualified MATERIALIZE restores verified bytes and skips process creation.
- Wrong-node/stale history fails closed to real RUN.
- Projection-only `reuse-index.json` cannot authorize reuse.
- Public/spec metadata `exec_scheduler_enabled=false` cannot bypass the scheduler boundary.
- Normal CLI execution physically writes execution history/projection files and stores private RUN attachment refs with byte hashes.

## Findings disposition summary

| Finding | Status after v2 | Rationale |
|---|---|---|
| `EXEC-A01` | `DEFERRED_TO_R02B` | ValidationSession still owns validation phase/attempt/progress state; R02A does not claim closure. |
| `EXEC-A02` | `DEFERRED_TO_R02B` | Validation durable state remains a projection/validation concern for R02B. |
| `EXEC-A04` | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` | Normal scheduler consults RER/RUN before spawn and honors terminal no-spawn dispositions. |
| `EXEC-A05` | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` | Reuse candidates are rebuilt from EXO/RUN execution history, not projection caches. |
| `EXEC-A06` | `PARTIALLY_ADOPTED` | Workflow/direct path is adopted; validation/test consumers remain for R02B. |
| `EXEC-A07` | `IMPLEMENTED_AND_PRODUCTION_ADOPTED` | MATERIALIZE restores verified bytes with no process spawn. |
| `EXEC-A08` | `DEFERRED_TO_R02B_OR_LATER` | Universal fresh/rerun/rerun-from controls are not finished here. |
| `EXEC-A13` | `PARTIALLY_ADOPTED` | `run_specs` has the shared scheduler boundary; all domain cache/reuse/resume paths still need the later audit/gate loop. |

