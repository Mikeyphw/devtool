# RM-08R2 — promise-by-promise behavioral audit

This audit is bound to the complete post-RM-07 replacement overlay, not a stacked hotfix. **Delivered** means observed behavior in named tests; **bounded** means unsupported cases must refuse; **open** is not falsely counted as feature closure. External qualification is a separate ledger.

| Original promise or implementation criterion | Assessment | Actual behavioral verification | Remaining limits |
| --- | --- | --- | --- |
| Nested Go tracked module/root `go -C` | **Delivered (bounded)** | `test_nested_go_command_uses_exact_tracked_module_and_pinned_setup` verifies chosen module and emitted setup pin | Native Go grammar is reviewed, not arbitrary shell |
| Go workspace member tracking, local dependency ordering | **Delivered (bounded)** | `test_local_go_workspace_requirements_replacements_and_cycles` reviews edges/order, refuses cycles and escape; native graph checks producer edges | Remote and unpinned replace refuse; no automatic DAG mutation |
| Source-bound workspace/lock integrity | **Delivered** | Original tracked/symlink/source drift tests; `go_workspace_manifest` source hashes | Not remote producer authentication |
| Go SDK versions and matrix | **Delivered (bounded)** | `test_go_bounded_real_version_matrix_and_isolated_jobs` emits pinned version cells with unique artifacts and negative version checks | 1–3 configured versions, min go.mod version; no arbitrary fleet |
| Reviewed Go build/test/vet/race/CGO/GOOS-ARCH | **Delivered (bounded)** | Original race/CGO host refusals, cross-compiled actual Go receipt, wrong OS/arch/CGO rejection | No unsupported CGO cross compiler installed |
| Go binary metadata, module receipts, repeat reproducibility | **Delivered (opt-in bounded)** | `test_nested_real_go_build_distribution_receipt_and_wrong_source`; `test_go_repeat_build_executes_twice_and_detects_modified_candidate` actual 3 Go build bytes / tampering | Repeat build requires CGO=0 and deterministic flags; not formal provenance |
| Python backend pin and ambiguous-backend refusal | **Delivered (bounded)** | `test_python_reviewed_backend_lock_and_version_pin`, unknown backend/missing hashes refuse | Accepted registry limited; not all third-party builders tested independently |
| pip/uv/Poetry/PDM dependency policy | **Delivered via reviewed hash export, not native manager execution** | `test_reviewed_manager_export_hash_binding_and_drift` positive/negative per manager; pip hash test | Native uv/Poetry/PDM sync/install and Python caching remain **OPEN** |
| Python multi-version host matrix | **Delivered (bounded)** | `test_python_three_way_version_matrix_multi_os_and_bounds` emits 6 isolated Ubuntu/macOS cells with three pinned setup Python versions | No Windows runner, caps 2 hosts / 3 versions / 48 jobs |
| Python coverage collection and aggregation | **Partial** | `test_native_owned_coverage_command_and_report_output` verifies commands, ownership and refusal; original coverage XML aggregation tests inspect real values | Instrumentation/report must be native-owned; no implicit auto-wrapping or independent CI evidence |
| Wheel and sdist static structure | **Delivered (local static)** | `tests/test_next04_distributions.py` exercises RECORD/package and archive integrity | Cryptographic signing/authentication not implied |
| Wheel offline install and console entrypoint | **Delivered (opt-in)** | `test_offline_isolated_wheel_install_and_entrypoint` real venv install/execution, tamper rejection | No unreviewed dependency installs, requires one reviewed entrypoint |
| Sdist offline build/install + console entrypoint | **Delivered (opt-in bounded)** | `test_real_offline_sdist_build_install_with_hash_pinned_backend` actually provisions synthetic pinned backend wheel, builds PEP517 wheel from sdist and invokes script; tamper rejected | Requires opt-in, offline wheelhouse and hash-locked dependencies |
| Native DAG node ownership and source-bounded byte handoff | **Delivered in tested scope** | Original RM-06 graph handoff tests plus version-cell owner refs; provider plan binds config/source | No cross-version handoffs, no independent GHA attestation |
| Independently accepted hosted/consumer/release gates | **OPEN, external** | Outputs explicitly `release_qualified=false` and `authenticated=false` | Requires observed hosted and consumer acceptance, separate from engineering |

## Inherited work not claimed closed by this overlay

RM-06 source-ownership affected-target pruning remains open; RM-07 Windows execution and Python dependency caching remain open. Their full-DAG / cache-disabled / unsupported-host refusals remain the safe behavior. RM-09 and RM-10 are separate future implementation packages under the two-overlay merge-window policy.

## Test obligations

Required installer validator includes `tests/test_rm08_completion.py`, `tests/test_rm08_projects.py`, RM-07/06 graph/profile, NEXT-03/04 providers/distributions, RM-05 artifact convergence and generated CLI registry. A passing local suite does not establish Termux or GitHub acceptance. Independent source-bound wrong-tree refusal, forced failure rollback, and clean positive install are separately required before final delivery.


### RM-08R3 Termux regression remedy
- Required NEXT-04 real Go distribution test now cross-builds Linux/amd64 explicitly and injects Android/ARM64 defaults during test execution; the production verifier remains fail-closed. The original broader promise checklist and its open cells are unchanged. A real Termux installation pass is still required.


### RM-08R4 promise-by-promise delta (2026-10-22)

| Promise | Demonstrated implementation, not file presence | Status |
| --- | --- | --- |
| Native pytest test collection | Real `run_node` executes a pinned `python -m pytest` target-local spec instrumented via coverage.py, then generates XML; both steps are subject to `run_specs` failure propagation | DELIVERED (opt-in, multi-job, source-bound) |
| XML evidence and producer binding | Real coverage XML parses under strict line/file identity rules, with declared `dist/coverage.xml` staged as an owned SHA-256 output | DELIVERED (local); remote provenance not implied |
| No unsafe fallback | Reject absent/forged coverage lock, unowned output, incompatible command, stale output and single-job exporter instead of skipping instrumentation | DELIVERED |
| uv/Poetry/PDM reviewed lock policies | Hash-bound pip export must match locked package identities; no hidden unreviewed native frontend execution | DELIVERED within original roadmap's policy scope; native frontend execution still OPEN as follow-up enhancement |
| All prior RM-08 promises | R3 full positive/negative Go workspace/module, Go version/cross-target/repro, Python build/sdist/wheel/matrices smoke tests are retained unchanged | DELIVERED/BOUNDED as previously audited |
| Real hosted/Termux external qualification | Local regression does not establish independent hosted or device authority | PENDING |
