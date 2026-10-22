# RM-06 promise audit — Devtool native DAG GitHub exporter (2026-10-22)

**Implementation status: PARTIAL (scoped RM-06 graph bridge); not formally closed.** Tests below are local behavior proofs, not independent hosted acceptance.

| RM-06 promise | State | Demonstration or deliberately open boundary |
|---|---|---|
| Compile native target-owned DAG, not new scheduler | PASS | `compile_graph`, `build_direct_job_specs`, `run_specs`; unit test plus real target-local Python test, `test_real_native_target_job_executes_and_zero_tests_do_not_pass` |
| Preserve node identity and dependencies | PASS | Frozen graph plan and native mapping; `test_compiles_graph_edges_and_independent_siblings` |
| A→C, independent B no fake A→B | PASS | `test_branching_dag_keeps_only_real_needs_edges` |
| Generate precise GitHub `needs` | PASS | Inspect YAML and graph tests |
| Pin hosted setup and Actions runtime | PASS, local | SHA-pinned checkout/setup-python/setup-go/upload/download and pinned Devtool source; no remote proof |
| Preserve exact consumer checkout source SHA | PASS, local | HEAD compared against GITHUB_SHA in runtime; recorded in handoff |
| Freeze source-config/toolchain/graph content | PASS | Existing `provider_plan` plus graph SHA recompile before executing |
| Preserve GitHub run attempt, producer and workflow code digest | PASS | `receipt.json` fields checked by `_load_manifest` |
| Typed explicit per-node file handoff | PASS, local | `stage_outputs`, `_load_manifest`, `consume_handoffs` and test round-trip |
| Reject wrong source/run/attempt/producer | PASS | Handoff negative tests |
| Reject byte tamper and size inconsistency | PASS | Handoff negative tests |
| Refuse symlink/traversal/overwrites | PASS | Negative file/path checks; all files validated before writes |
| Reject multiple producers/implicit generated dependency | PASS | Graph compiler collision/ownership checks |
| Reject unsupported host/device/root/signing/commands | PASS via prior provider | Existing `provider_plan` admission reused; no relaxation |
| Stable optional single-job fallback | PASS | Default `provider_export`, preserved prior tests |
| Bound multi-job node/file/byte fanout | PASS | 24 nodes, 64 file outputs/node, 512 MiB handoff cap |
| Read-only `--mode multi-job --plan`, explicit `--yes` and `--verify` | PASS | Canonical parser + CLI generated surfaces and export tests |
| OS/arch/version **opt-in reviewed multi-cell matrices** | **OPEN RM-07** | Only pinned Ubuntu-24.04 x86_64 cell; refuse unsupported |
| Lockfile-derived safe cache keys | **OPEN RM-07** | Cache disabled; no cache secrets or injection possible through generated workflow |
| Owner-proof affected-target filtering | **OPEN RM-07/RM-20** | Explicit full DAG; no unproven target pruning |
| Authenticated producer identity from GitHub | **NOT CLAIMED** | SHA digests are integrity checks, not independently authenticated attestations |
| Real GitHub workflow dispatch and artifact acceptance | **NOT RUN** | Requires independently authenticated hosted execution |
| Device/signing/release authorization | **NOT CLAIMED** | Outside roadmap implementation/qualification scope |

**Merge window RM-06/RM-07 decision:** Separate rollback boundary. RM-06 provides native graph/handoff contracts and a conservative reviewed Ubuntu host. RM-07 consumes that compiler boundary and owns version profiles, multi-host admission, matrix limits, cache/lockfile policy and provider doctor. Do not silently mark the open RM-06 matrix/cache/selection promises complete because core DAG tests passed.
