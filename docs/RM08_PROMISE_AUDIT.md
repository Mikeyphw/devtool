# RM-08 promise-by-promise source audit (2026-10-22)

The RM-08/RM-09 two-overlay merge window was assessed first. **No merge**: RM-08 extends reviewed Go and Python providers; RM-09 needs a new .NET provider, multi-solution restoration graph and Windows/RID behavior. These are not one safely reviewable transaction. RM-09 remains open.

Status words are deliberately narrow: **DELIVERED** means implemented and exercised by the cited behavioral test; **PARTIAL** means a real working subset plus a listed remaining obligation; **OPEN** means absent. A GitHub workflow emitted locally is not independent GitHub-hosted evidence.

| Roadmap / overlay promise | Status | Implementation and actual behavioral evidence | Still missing |
| --- | --- | --- | --- |
| Go tracked nested module discovery | **DELIVERED** | `go_workspace_manifest()` loads tracked nested `go.mod` files and binds each module/version/path/digest; `test_nested_go_command_uses_exact_tracked_module_and_pinned_setup` verifies actual `go -C` admission and both single/multi-job setup output. | None within bounded reviewed command grammar. |
| Go workspace dependencies (`go.work`) | **PARTIAL** | `test_workspace_members_and_escape_refusal` exercises multi-line `use`, source digest drift, path traversal, external replacement and untracked entries. `test_go_module_replace_and_workspace_version_mismatch_refuse` rejects inconsistent version. | Arbitrary `replace`, remote modules, workspace graph execution/order and multi-version workspaces deliberately refuse. |
| Go versions / host matrix | **PARTIAL** | Selected nested `go.mod` determines pinned action `go-version-file`, and `GOTOOLCHAIN=local` forbids implicit installation; original RM-07 two-profile fanout remains. | Multi-version Go SDK matrix within one target remains absent; independent hosted runner unavailable locally. |
| Go test/vet/build/race profiles | **PARTIAL** | Existing reviewed commands remain; `test_go_race_requires_declared_cgo_and_linux_host` checks `test -race`, declared CGO and Linux-only host, preventing macOS promotion. | Substantive race run on actual hosted runner and wider CGO toolchain coverage. |
| Explicit GOOS/GOARCH/CGO eligibility | **DELIVERED (bounded)** | Existing RM-06/07 test suite checks host/cross-target admission; `test_nested_real_go_build_distribution_receipt_and_wrong_source` runs a real compiled Go candidate and checks module/OS/arch/CGO buildinfo. | Nondefault target toolchains not implicitly supported. |
| Go executable receipts / module distributions | **PARTIAL** | Real nested build and `verify_distributions` confirms expected module, binary header, buildinfo, digest and Git source integrity; wrong tracked source rejected. Nested candidates stay inside selected module `dist/`. | Deterministic repeat-build reproducibility attestation, independent source provenance, code signing and release qualification. |
| Python explicit build backend | **PARTIAL** | Opt-in `ci_python_policy="reviewed"` validates allowlisted backend, no `backend-path`, pinned build requirements; `test_python_reviewed_backend_lock_and_version_pin` rejects unknown backend. Legacy V1 flows remain labeled unqualified. | Additional backends and complete build-system frontend behavior. |
| Python pip/uv/poetry/pdm dependency policy | **PARTIAL** | Exactly one tracked manager lock; reviewed **pip** hash-locked install and pinned backend closure exercised in `test_reviewed_python_hosted_wheel_smoke_is_owned_and_hash_provisioned`. uv/poetry/pdm identities discovered and deliberately refused for hosted execution. | Reviewed offline installers for uv, poetry and PDM; Python dependency caching. |
| Python bounded versions / matrices | **PARTIAL** | Per-job `ci_python_version=3.11/3.12/3.13` is validated and actually printed in single/multi-job workflow; unsupported version rejects. | Multi-Python-version fanout in one workflow, version-specific dependency validation on all runner OSes. |
| Python coverage reports / aggregation | **PARTIAL** | `merge_coverage_xml()` parses bounded coverage.py XML, merges disjoint line counts and rejects overlapping file identity/unsafe XML. `test_python_coverage_merges_disjoint_files_and_refuses_duplicate_or_entity` tests values/refusal. | Automatically discover/run coverage instrumentation and attach merged report to native test evidence. |
| Python wheel/sdist metadata verification | **DELIVERED (local static)** | Existing NEXT-04 `verify_wheel()` and `verify_sdist()` inspect RECORD, tags, entrypoints, sdist metadata; regression tests verify failures and source pin. | Not a signature/hosted authority. |
| Isolated install-and-invoke wheel | **DELIVERED (opt-in)** | `smoke_wheel()` verifies wheel bytes, installs offline using `--no-index --no-deps` into a disposable virtualenv, invokes declared script with `--help`, returns negative trust fields. Real `test_offline_isolated_wheel_install_and_entrypoint` and tampered-wheel refusal, plus native-node wiring guarded by `ci_wheel_smoke=true` and one owned wheel/entrypoint. | Not every console script supports `--help`; multi-entrypoint smoke contract and installable sdist smoke remain open. |
| Go/Python native DAG ownership | **DELIVERED (bounded)** | RM-06 direct-job compiler still owns execution; RM-08 checks exact package-output owner and opted-in smoke against source-bound distribution. Regression rechecks both exporters. | No new/parallel scheduler or arbitrary remote command authority. |
| Release/hosted authentication | **OPEN (external acceptance)** | Flags explicitly remain `release_qualified=false`, `remote_attested=false`. All tests are local/disposable. | Independently observed GitHub jobs and authenticated artifact producer; consumer/native device/signing/release gates. |

## Cross-overlay regression of inherited promises

| RM-06 or RM-07 obligation | Current state and substantive evidence |
| --- | --- |
| RM-06 node graph / independent edges | **DELIVERED** within Go/Python host jobs; `test_rm06_graph_export.py` exercises actual `needs` projection and runner invocation; RM-08 retains same code path and adds host-specific toolchain pin. |
| RM-06 byte handoff before consumer execution | **DELIVERED** within unsigned source/run/attempt model; RM-06 tests tamper and wrong-attempt rejection. Independent GitHub authority remains OPEN. |
| RM-06 safe multi-host/refusals | **PARTIAL**; RM-07 macOS/Ubuntu cells re-admit host requirements. Windows and privileged Android/device nodes refuse; Python mixed-version fanout remains OPEN. |
| RM-06 source ownership affected-target pruning | **OPEN**; full DAG remains the fail-safe fallback. |
| RM-07 pinned tools and requested versions | **PARTIAL**: SHA pins plus reviewed Go source pin, new bounded Python pins. SDK/version/arch breadth beyond those records OPEN. |
| RM-07 cache trust and permission isolation | **PARTIAL**: Go compiler cache opt-in with tracked root lockfiles; nested Go cache may refuse. Python cache disabled; unsafe lock and credential paths refused. |
| RM-07 read-only capability doctor | **DELIVERED** within recorded providers; tests distinguish missing/unknown/installed and prevent installs. No hosted availability inferred. |
| RM-07 Windows/Termux execution | **OPEN** for hosted Windows, deliberately refused for local-only Termux; no false acceptance. |

**Engineering state:** RM-08 **PARTIAL** (implemented, source-bound, tested locally). RM-06 and RM-07 remain **PARTIAL** until their explicit open promises close. RM-09 is **NOT STARTED**. Device, consumer, remote host, signing and release authority remain separate and unqualified.


## RM-08R1 Android/Termux regression correction (2026-10-22)

**Observed device failure:** The actual Termux installer ran 7 passing RM-08 tests, then the real Go distribution test failed at ELF architecture validation: the fixture declared `linux/amd64` but the build command inherited the device Go toolchain's default target (`android/arm64` on many devices). The Linux/amd64 verifier correctly refused that mismatch. The earlier local Linux/amd64 validation had not covered an ARM64/Android Go build host.

**Repair:** The real test fixture now supplies `GOOS=linux`, `GOARCH=amd64`, `CGO_ENABLED=0`, `GOTOOLCHAIN=local` to its executable `go -C ... build` invocation. Post-build assertions check that the compiled artifact is accepted only under the declared Linux/amd64 and no-CGO contract and that it is refused for a mismatched ARM64 or CGO-enabled declaration. Source modification after the build must still fail distribution verification. Production format/target policy remains unchanged; no Android binary is admitted as Linux by error recovery.

**Scope:** This fixes the host-ambient test assumption, not Go cross-toolchain availability on every Android installation. The installer remains source-bound to clean post-RM-07 and all RM-08 partial/open statuses above remain in force. A passing local repair does not supersede the user's direct-device evidence; the Termux installation must be rerun for device acceptance.


## RM-08R2 behavioral completion overlay

The earlier RM-08/R1 table is preserved as history, not overwritten or retrospectively called complete. See `RM08_COMPLETION_PROMISES.md` for updated delivered/bounded/partial/open verdicts and direct behavioral evidence. The consolidated replacement also includes the original Termux GOOS/GOARCH portability fix and retains its strict architecture verifier.


## RM-08R3 portability re-audit (2026-10-22)

| Promise / regression | Implemented behavior | Evidence | Status |
| --- | --- | --- | --- |
| Real NEXT-04 Linux/amd64 Go distribution regardless of Termux defaults | Legacy `build_go()` pins GOOS, GOARCH, CGO, GOTOOLCHAIN; receipt verifier unchanged | `test_real_go_build_and_verification_with_source_pin` poisons inherited GOOS=android, GOARCH=arm64 and validates real ELF/buildinfo | Fixed in replacement; device pass pending |
| Strict wrong architecture/CGO refusals | No production verifier relaxation | `test_wrong_go_architecture_and_cgo_rejected`, RM-08 nested-receipt negative checks | Preserved |
| All original Go/Python provider scope | Same RM-08R2 implementation and admitted limitations | Full `docs/RM08_COMPLETION_PROMISES.md` checklist and mandatory multi-suite validator | Native manager frontend execution and automatic Python coverage still OPEN; do not mark RM-08 closed |
| Independent hosted/device/release authority | No claims from local tests or receipt bytes | Source-bound logs, explicit non-qualified status | External acceptance OPEN |


### RM-08R4 automatic coverage behavior audit (2026-10-22)

- **Delivered:** `ci_python_auto_coverage=true` requires hash-bound coverage in the tracked reviewed Python installer lock, exact native `python -m pytest`, output ownership, multi-job export. The compiled graph carries the opt-in in its digest; `run_node` instrumented test + XML generate and verifier/hand-off are exercised end-to-end.
- **Negative:** Wrong command, missing dependency, comment-only forged coverage pin, missing output, preexisting output and single-job export are rejected. No test may pass solely because XML exists.
- **Open:** Native standalone uv/Poetry/PDM frontends. Their lockfiles alone are **not** proof that their own executables ran. Hosted/device acceptance remains external.
