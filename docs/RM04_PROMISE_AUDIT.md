# RM-04 promise audit — implementation, not artifact presence

Baseline: **post-RM-03 Git tree `18b66b092f84f0d31dd1521af866f68a27cdca73`**. Native target: `devtool`. All statements below describe behavior implemented in this overlay; installation acceptance on the user's Termux remains separate.

| ID | Promised behavior | Behavioral proof |
|---|---|---|
| P01 | RM-04 / RM-05 merge window assessed | RM-04 handles replay; RM-05's format-aware verifier remains separate |
| P02 | Native canonical CLI `workflow retry --run-id --failed --plan` | Parser and isolated CLI subprocess read-only plan test |
| P03 | Explicit `--yes` required for replay | Rejection test without approval |
| P04 | Normal native DAG run freezes source/config/inputs before process execution | Real two-node scheduler execution writes matching records |
| P05 | Frozen DAG identity, node dependencies and original attempts | Signature- and graph-drift rejection tests |
| P06 | EXO snapshot + complete terminal event journal required | Truncated-journal rejection and RM-03 compatibility |
| P07 | Exact Git commit/tree and clean tracked source required | Mutated tracked input rejection |
| P08 | Host/toolchain executable identity enforced | Host mismatch and executable digest checks |
| P09 | Input file and glob content bound before replay | Untracked input mutation changes glob fingerprint; replay blocked |
| P10 | Prior success reused only with matching output bytes | Independent A reused; tampered A invalidated |
| P11 | Upstream reruns invalidate dependent successful nodes | Dependency closure algorithm, not static wave grouping |
| P12 | Rerun failed/blocked nodes only when declared safe | Unsafe job test blocks mutation |
| P13 | No automatic signing, deployment, remote or device dispatch | Command/metadata guard and explicit CLI behavior |
| P14 | Native scheduler executes only selected nodes | Targeted B/C replay test; A never spawned |
| P15 | One incremented native attempt identity and parent EXO run reference | Replay spec-identity assertions |
| P16 | Durable private per-node start/terminal checkpoints | Interrupted B checkpoint retained; full native callback path |
| P17 | Interrupted retry requires explicit `--resume --yes` | Simulated interrupt and refusal to replay without resume |
| P18 | Resume verifies completed outputs and never duplicates completed nodes | Partial B finished, resume executes C only; tampering blocked |
| P19 | Completed attempt refuses duplicate execution | Duplicate-final-attempt negative test |
| P20 | Local file proof never authorizes remote or release qualification | No trust promotion interfaces; original RM-03 regressions |
| P21 | Mandatory in-process `devtool` target-graph validation | Overlay validator imports config + graph planner and refuses absent target |
| P22 | Clean exact-source guard and rollback after validation failure | Disposable Devtool apply and forced-failure rollback checks |
| P23 | RM-00–RM-03 scheduler and event compatibility | Regression suites execute after overlay apply |
| P24 | Canonical generated help/completions/reference stay in sync | Regenerated `share/core-generated/*`, wired `--resume` and per-node CLI callback, and fixed-point/CLI tests |

**Important limitation:** a run without a complete original retry manifest must be refused, not guessed from historical logs. Old runs do not acquire retroactive retry eligibility. A plan marked `READY` proves eligibility to attempt bounded local execution, *not* that any future command has succeeded or is release-qualified.
