# Devtool 1.1.0 — source-audited roadmap for remaining capabilities

**Audit:** 2026-10-21. **Planning baseline:** user-supplied `devtool-20261021-015645-939512.tar.gz`, branch `feature/rmp01-security-remediation`, clean snapshot at commit `b045f65eb44b9e7b7ca25f55f9a60c7902d60671`. **Inputs:** `DEVTOOL_CAPABILITY_AUDIT_20261021.md`, `DEVTOOL_IMPROVEMENT_BACKLOG.md`, repository source, `docs/DEVTOOL_IMPLEMENTATION_ROADMAP.md`, `docs/DEVTOOL_IMPROVEMENTS.md`, `docs/DEVTOOL_IMPROVEMENT_INDEX.json`, relevant Actions/federation/qualification docs and tests. No live GitHub, native Termux, SSH worker, Android device, signer, installation, release or cross-repository migration was exercised in this audit.

## 1. Decision and current-state summary

This is an **extension to the repository's existing R0–R9 roadmap**, not a replacement. Existing owners already implement named DAG planning and resource-aware scheduling (`workflow_plan_views.py`, `workflow_scheduler.py`), bounded A03/A09 Python-shell/Go GitHub Actions adoption (`actions_adoption.py`), read-only R04 lineage (`actions_lineage.py`), Android artifact role inspection (`actions_android.py`), read-only overlay recovery planning (`overlay/recovery.py`), source-bound federation DAG contracts (`federation_dag_contract.py`), R07 SSH return-evidence improvements, and R08/R09 security/acceptance inventory. Preserve these; do not create parallel schedulers, dispatchers, artifact registries, qualification authorities, or transaction stores.

**DT-001–DT-055 audit disposition at this source:** 3 implemented (DT-009/012/019), 30 partial, 21 open, 1 unverified (DT-014). This is a source-capability classification, **not** 52 failed tests or 52 equally urgent tasks. DT-031–034 descriptions remain provisional because their original complete backlog headings were not provided. **New DT-056:** the in-repository traceability index lists DT-001–023 but not DT-024–055, although the later external backlog lists them and the source roadmap's DT crosswalk also stops at DT-023. Repair with an additive, history-preserving index reconciliation.

**Independent external acceptance is still outstanding.** Read-only `python -m devtool.rmp_r09_campaign --repo . --format text` on this extracted commit returned `PASS_R09_INVENTORY_TESTS_NOT_RUN_EXTERNAL_OPEN`, with all `source_pass/native_pass/hosted_pass/consumer_pass/device_pass/security_formal_pass/formal_release_qualified = false`. This means inventory worked; tests were not requested by that R09 invocation. Separately, 139 focused pytest cases passed, four R05 Textual UI tests skipped because `textual` is unavailable in this environment. An initial R08/R09 fixture setup failed because the archive's `.git` ownership was untrusted to local Git; adding the exact extracted repository and `.git` paths to an *isolated temporary Git configuration*, without changing the source, allowed all 19 R08/R09 focused tests to pass. This is an inspection-environment issue, not a demonstrated Devtool defect. These focused tests **do not** constitute R09 source/native/full acceptance.

### What is already implemented: do not schedule a rewrite

| IDs | Existing capability | Remaining acceptance only |
|---|---|---|
| DT-009, DT-012, DT-019 | Named workflow `devtool plan`, ASCII/Mermaid/DOT/JSON, explainable scheduling waves | Regression on the chosen current source and representative consumers; extend only if a concrete gap is found |
| Partial DT-006/007/010/011/017/020/023/035/042/043/051/052/053 | Source/run lineage, dispatch-once protection, archive checks, A03/A09 hosted adoption, qualification barriers, overlay `recovery-plan`, fixture G5, bounded Go admission, target DAG and source identity | Extend within the existing ownership and contracts rather than introducing incompatible replacements |
| Existing R07/R08/R09 | SSH evidence checks, RMP current-source security inventory and independently gated cross-campaign acceptance | Native and independent external receipts; no source-only promotion |

## 2. Non-negotiable design invariants

1. **Version control and source identity:** Every write uses current clean source, original file SHA-256, authoritative Git object identity and a durable plan. Do not apply against b045f65 after the user's checkout has changed without a fresh current-source rebase/preflight.
2. **Git safety:** Never silently `reset --hard`, update refs, integrate a stale isolated worktree, force-push, overwrite unrelated changes, discard ignored user data or remove evidence. Historical retirement is not transaction completion or rollback.
3. **Remote safety:** Exactly one GitHub POST per reviewed request; uncertain outcome remains uncertain. Resumption checks existing identity/receipts; source/attempt/artifact identity must match exactly. No guessed dispatch correlation.
4. **Qualification:** Distinguish source-implemented, locally tested, installed-wheel tested, native Termux, hosted runner, independent worker, consumer, Android/device, signer and formal release. No upward promotion from SHA-256 alone, passing CI, a provided JSON receipt, an adb connection, or a local PRoot running on the same phone.
5. **Host eligibility:** Explicit runner/OS/arch/toolchain/device/root/signing admission on *every* generated CI DAG job; unknown = rejected or not-run, never treated as supported.
6. **Existing Devtool architecture:** Shared DAG/EXO; existing Actions dispatch/pipeline; existing artifact registry and Git transaction journal; generated CI must consume existing declared jobs rather than duplicating shell workflows.
7. **UX:** Narrow Termux-friendly output and JSON parity; all mutating operations require explicit opt-in, with preview and reasoned failure messages. No speculative background work.
8. **Release ownership:** `formal_release_qualified=false` until an independent policy authority approves it. Preserve R08 formal N01/N02/C04/C05/C06/Q01/Q02/U01 and six original external-substrate obligations.

## 3. Proposed execution graph

```text
NEXT-00 inventory, exact-source baseline, documentation (parallel quick fixes)
  ├── NEXT-01 historical transaction retirement and recovery (critical safety)
  │      └── NEXT-05 safe multi-repository adoption and wrapper atomicity
  │              └── NEXT-06 first-class cross-repository extraction and pruning
  ├── NEXT-02 typed evidence/eligibility/qualification contract
  │      └── NEXT-03 extensible hosted language-provider interface
  │              ├── NEXT-04 Go + Python providers and real projects
  │              ├── NEXT-07 .NET solution matrix
  │              └── NEXT-08 Rust / JS / JVM / C++ / Shell providers
  ├── NEXT-09 Android discovery and explicit custom variants
  └── NEXT-10 operator wrapper, diagnostics and adoption UX
NEXT-00 through NEXT-10 + independently collected R09 evidence
  └── NEXT-11 cross-consumer acceptance / source-bound release readiness

R09 external evidence collection is a separate parallel track throughout.
```

`NEXT-*` labels avoid changing historical R0–R9 claims. Phases can be implemented in coherent independent batches; this graph declares hard prerequisites, not a forced serial rollout.

## 4. Phase specifications and hard acceptance

### NEXT-00 — Baseline, ledger reconciliation, and cheap correctness fixes (P0, first)

**IDs:** DT-013, DT-014, DT-028, **DT-056**. **Touch:** `docs/DEVTOOL_IMPLEMENTATION_ROADMAP.md`, `docs/DEVTOOL_IMPROVEMENTS.md`, `docs/DEVTOOL_IMPROVEMENT_INDEX.json`, `docs/actions-06-pipeline.md`, CLI bootstrap/overlay input handling and tests.

* Import all DT-001–DT-055 plus DT-056 into the canonical index using immutable historical metadata and an additive `current_source_assessment` with exact baseline SHA, source status, qualification status, evidence paths and target milestone. Do **not** rewrite the index's original R01 historical baseline SHA or its protected external gate rows.
* Fix the confirmed `docs/actions-06-pipeline.md` example: it copies to `.devtool/actions-pipeline.json` and then instructs `git add` without `-f`, while `.gitignore` ignores `.devtool/`. Prefer a tracked project manifest path under `docs/ci/`; add a clean fixture that runs the exact documentation example.
* Test whether `apply-overlay` consumes the input ZIP on repeat install / retry; document actual semantics and add opt-in `--keep-input` only if needed. Don't infer behavior from an earlier anecdote.
* Provide a dependency-light, no-HUD isolated CLI/bootstrap doctor that emits full local import traceback with classification, without authorizing repository mutation. Distinguish unknown import failure from an overlay defect.
* Re-run fixed current-source tests and installed-wheel CLI/schema smoke. Capture skips and unavailable host tools separately from failures.

**Gate:** index covers exactly the reconciled DT set without duplicate IDs; historical baselines remain unchanged; clean copied docs commit succeeds; ZIP lifecycle and bootstrap-negative fixtures have deterministic behavior; 0 unrelated source mutations.

### NEXT-01 — Safe historical transaction retirement (P0 security/safety)

**IDs:** DT-020, DT-029, DT-030. **Owners:** `devtool/transactions.py`, `devtool/overlay/recovery.py`, transaction Git journal and CLI. **Consumer fixture:** old MMRL `rollback-failed` isolated-worktree journal with a newer clean HEAD (never replay a device journal as a local truth source).

* Add `devtool transaction inspect` and explicit `retire --preserve-current --dry-run`, `retire --preserve-current --yes`, and reversible `unretire` semantics after reviewing command naming consistency. Distinguish applied, interrupted, rolled-back, abandoned, superseded and uncertain journals.
* Verify exact journal/marker/backups, process liveness, old/new HEAD ancestry, index bytes, worktree cleanliness, affected path states, symlinks and source boundaries. Quarantine active markers with a durable reversible escrow; preserve original journal/backups and retained receipts.
* **Never** call `integrate_transaction_worktree`, `git reset --hard`, `git update-ref` or cleanup hooks from the retirement path. Current `abandon_transaction()` calls integration for isolated journals; it must remain a different operation.
* Keep `overlay recovery-plan` read-only and add exact safe next-step guidance. Do not auto-retire active or un-restored transactions.

**Gate:** disposable ahead-of-HEAD fixture retires and unretires correctly with byte-identical `HEAD`, index and worktree; forged marker, running PID, dirty tree, missing backup, symlink, wrong SHA and unresolved restoration fail closed; normal successful worktree integration behavior remains unchanged.

### NEXT-02 — Shared eligibility, provenance and test-evidence kernel (P0 architecture)

**IDs:** DT-006, DT-010, DT-017, DT-023, DT-051, DT-052, DT-054. **Owners:** `actions_lineage.py`, `actions_qualification.py`, `actions_artifacts.py`, `artifact_registry.py`, `workflow_graph.py`, `workflow_scheduler.py`, `federation_dag_contract.py`, `actions_pipeline.py`, EXO result/runner adapters.

* Version a **typed capability/evidence contract**: source repo+commit/tree, target/job/DAG node, dispatch request ID, GitHub workflow/run/attempt, runner OS/architecture/toolchain, artifact role/type/path/hash, signature state, test report and test count, coverage, SBOM/provenance claims, trust authority, gate status and reason.
* A provenance record is descriptive unless validated by its trusted producer: a file hash is integrity, not authenticity; a successful GitHub job is not Android instrumentation, native worker or release approval. Explicitly represent `NOT_RUN`, `UNKNOWN`, `WAITING`, `BLOCKED`, `PASS`, `FAIL` in the appropriate domain.
* Attach host eligibility to every graph node and refuse unsupported runner/OS/arch/root/device/signing requirements before generation/dispatch; preserve existing safe DAG scheduling semantics.
* Normalize pytest/JUnit XML, Go test JSON, TRX, Cargo and JS test formats through adapters. Missing reports, zero tests where tests were required, parse errors and failing cases must never silently pass.
* Add `devtool artifacts verify` as a typed, read-only aggregation surface over existing producer-specific verifiers; avoid treating ZIP digest validation as APK signature or executable distribution identity.
* Extend read-only lineage to EXO/federation + Actions + verified outputs with exact identity joins; external API observations remain separate and untrusted until independently verified.

**Gate:** per-domain schema and falsification fixtures; wrong-source/wrong-attempt/changed-hash/untrusted report rejected; source-only CI-presence never promotes build/artifact/device/release; normalizers verify actual test count and failing cases; existing R04/R06/A03/A09 tests green.

### NEXT-03 — Extensible Actions language-provider API and DAG export (P0)

**IDs:** DT-035, DT-041, DT-042, DT-053, DT-055. **Owners:** `actions_adoption.py`, `actions_polyglot.py`, `actions_pipeline.py`, Actions workflow templates, config/doctor providers.

* Define versioned provider interface: project discovery, declared inputs/lockfiles, toolchain pin, runner/host eligibility, named Devtool DAG import, host-safe action translation, test report adapters, output roles and verification policy. Keep providers declarative, built-in/allowlisted, not arbitrary unreviewed executable plugin discovery.
* Refactor A03/A09 generated workflow into a bounded reusable exporter of existing target-local DAG jobs. GitHub job matrix, bounded dependency edges, artifact upload paths, retry and cache trust are generated only when provably host eligible. Unknown custom commands/shell expansion fail preflight.
* Preserve G5 fixture lane as compatibility proof but add **real consumer source** matrix acceptance. Matrix cell = source+workflow+attempt+toolchain+artifact evidence; maintain `qualified_release=false` by default.
* Make changed-target selection conservative (missing diff/base means run required affected jobs, not skip them), lock-aware caches non-privileged and fork/untrusted workflows safe.
* Produce `devtool capabilities`/language doctor evidence for each provider rather than a fabricated universal compatibility list.

**Gate:** at least two independently implemented provider adapters pass the same conformance/negative suite; A03/A09 jobs still work; no new separate CI scheduler; a non-eligible device/root/signer job cannot appear as runnable hosted CI.

### NEXT-04 — Full Go and Python providers (P0; first real-project proof)

**IDs:** DT-031, DT-033, DT-043, DT-044 (and NEXT-03 real-project acceptance). **Owners:** new language-specific adapters + existing Go/Python runners, A09 adoption generator, artifact verifiers. **Consumers to qualify when their current sources are supplied:** xbsctl or BKX for Go; dotcli or mike-cli-suite for Python.

* **Go:** detect `go.mod`/`go.work`, pinned Go version and module path; `go test`, `vet`, optional race where supported, explicit `GOOS/GOARCH`, CGO and host/cross compilation eligibility. Package platform-identified distributions; verify binary format/arch, digest and declared output identity (tests do not certify a runnable native Android/Termux binary).
* **Python:** inspect `pyproject.toml`, lockfiles/dependency policy, supported interpreter matrix, pytest/coverage/Ruff; build wheel/sdist in clean environment; verify wheel metadata, version/entrypoints and distribution digests using installed-wheel smoke. Do not promote from an untrusted sdist file listing.
* **Shell/dependency-light hooks:** basic preflight is not equivalent to a ShellCheck/Bats provider (NEXT-08). Preserve existing host admission and exact selected job argv contract.

**Gate:** one actual pinned consumer per provider with eligible host matrix, exact source/run/attempt, verified distribution, substantive test report and rejection of unsupported GOOS/arch/CGO, wheel metadata drift and missing lock policy. No silent downgrade to a generic shell job.

### NEXT-05 — Transactional single- and multi-repo adoption (P0/P1)

**IDs:** DT-004, DT-008, DT-011, DT-021, DT-024, DT-025, DT-026, DT-027, DT-032, DT-034. **Owners:** overlay planning/apply/transaction, `config_migration.py`, wrapper migration/initialization, repository identity/federation, Git commit policy.

* One **source-locked multi-repo plan** checks every selected checkout, remote/branch identity, existing transaction reservations, current paths/hashes, config migration and overlay equivalence **before** first mutation.
* States: `FRESH`, `APPLIED_UNCOMMITTED`, `COMMITTED`, `PARTIAL`, `DRIFTED`, `BLOCKED`; distinguish already installed expected outputs from a similar file with different provenance. Resume/skip are idempotent; a different directory basename alone should not reject a verified repository identity, but a renamed checkout cannot spoof a different remote.
* Stage legacy config migrations as part of owned transaction (preserve unknown/extension data and exact original bytes; refuse ambiguous keys); capture generated `devtoolw`, `devtoolw.cmd`, `.devtool-wrapper.lock` in the allowlisted same-repo commit without staging unrelated changes.
* Cross-repository *global atomic Git commit* is generally unavailable: expose prepare barriers, durable per-checkout commits and **honest partial failure** with source-pinned resume, never imply that a committed repository was rolled back when it wasn't. Do not rewrite third-party commits or push automatically.
* Preserve overlay source-bound rebase and existing rollback semantics. Plan/preview must be nonmutating, including no inferred source removal.

**Gate:** all six historical integration scenarios covered with disposable repositories; pristine/already-applied/partial/drifted, renamed checkout, interrupted commit, legacy config, wrapper verification, conflict and dirty-HEAD negatives; exactly one allowed new commit per successful checkout; interrupted Nth checkout reports earlier durable commits truthfully.

### NEXT-06 — Native two-repository extraction with generated-data awareness (P1)

**IDs:** DT-037, DT-038, DT-039, DT-040. **Owners:** new bounded migration planner utilizing existing federation/overlay/Git journal; target/dependency/wrapper/action registry. **Acceptance scenario:** `mike-cli-suite` → `xbsctl` split, requiring independent current-source inputs before execution.

* `migration plan` inventories git tracked content, ignored-but-generated content (`git ls-files`, `git check-ignore`), untracked unknowns, symbolic links, nested repos, `local.properties`, ownership paths, CI workflows, wrapper aliases, native jobs, docs and release references.
* `prepare` backs up **every** ignored/generated source-owned output outside either checkout with SHA-256 inventory, confirms destination remote identity and preserves Git history when requested. Unknown untracked source, unexpected symlinks, bad Go module paths and unverifiable build-stage files block mutation; do not delete broad ignored directories.
* `execute` checkpoints destination commit, source pruning and dependent DAG/wrapper/CI references with per-repo durability; avoid claiming cross-repo rollback after commit. Distinguish Go target `runner="command"` from legacy schema and repair inherited relative module assumptions.
* `resume/status` accurately identify prepared/committed/partial/blocked steps and source changes since prepare; explicit audits for generated output and dependent graph orphan removal.

**Gate:** repeatable disposable split with clean tracked state plus ignored generated outputs and `local.properties`; backup failure changes neither repository; hash tamper/symlink/unknown source fail; after successful split both independent repositories pass their declared Devtool workflows and the original has no dangling target/wrapper/CI references.

### NEXT-07 — .NET monorepo provider (P1)

**IDs:** DT-036, DT-045. **Owners:** existing .NET runner + NEXT-03 provider / NEXT-02 TRX adapter. **Consumers:** Sims3tools / Mike_SVMods after source qualification.

* Discover `.sln`/`.slnx`/projects, transitive project references and target framework/RID; resolve Linux-eligible versus Windows-only steps before host assignment. Reuse local EXO jobs for restore/build/test/publish, NuGet trust and lock-aware caches.
* Build solution-scoped DAG/matrix with inherited dependency failures, TRX test counts and verified publish output. Do not claim an Ubuntu runner qualified Windows-only code.

**Gate:** scoped solution change selection; Windows and Linux required lanes separately verified on a real pinned consumer; source-bound TRX and artifact identities; unsupported host -> blocked not green.

### NEXT-08 — Remaining hosted language providers (P1/P2)

**IDs:** DT-046, DT-047, DT-048, DT-049, DT-050. **Owners:** NEXT-03 provider interface and NEXT-02 reports. **Recommended order:** Rust → JS/TS → non-Android JVM → Shell → C/C++ (unless consumer demand changes).

* **Rust:** Cargo lock/workspace, pinned toolchains, `fmt`/Clippy/tests, target triples; actual package and Cargo report.
* **JS/TS:** npm/pnpm/yarn/bun only with an explicit lock/manager policy, Node matrix, lint/typecheck/tests, web/package outputs and bounded untrusted package scripts.
* **JVM:** non-Android Maven/Gradle with JDK matrix and JUnit/JAR; never confuse JVM library detection with Android app Gradle discovery.
* **Shell:** ShellCheck/shfmt/Bats plus Bash/Zsh host eligibility; Termux-only tasks require native Termux receipts, not hosted Ubuntu emulation.
* **C/C++:** CMake/Meson, compiler/ABI, CTest, sanitizer eligibility, binary provenance; no unsupported cross-arch claim.

**Gate:** one real source-controlled consumer per *claimed* language/OS and a negative admission test for each unsupported host/platform; no synthetic G5 fixture alone closes a real-project provider.

### NEXT-09 — Robust Android module and custom variant admission (P1; parallel after NEXT-00)

**IDs:** DT-002, DT-003, DT-005. **Owners:** `actions_android.py` and Android/Gradle declarative config, Actions templates. **Consumers:** Studyflow, relatorio-android, MMRL once provided on current source.

* Improve static `settings.gradle(.kts)` grammar for multi-line `include` and controlled `projectDir` mappings. Support declared convention-plugin metadata and a guarded user-provided module/application-ID override for dynamic Gradle logic. Do not execute Gradle script as an early trust preflight or infer `com.android.application` from a library.
* Accept only target-declared custom variants/format/signing roles, not arbitrary Gradle task strings. Normalize `OfficialDebug` vs `officialDebug` only with a verified mapping; preserve Android artifact verification, debug-only trust and separate release signer gates.
* Explain protected `org.gradle.jvmargs` override conflicts with exact declaration owner and reviewed transformation; never disable the Gradle protection automatically.

**Gate:** Studyflow convention plugin, relatorio multiline settings and MMRL custom variant fixture; reject library-only, unknown variant and forged package/expected signer; APK/AAB/instrumentation test role verification remains regression green.

### NEXT-10 — One operator journey and safe wrapper/doctor APIs (P1/P2)

**IDs:** DT-001, DT-007, DT-015, DT-016, DT-018, DT-022. **Owners:** typed wrapper config/resolver, `actions_operator.py`, `actions_workspace.py`, `actions_lineage.py`, CLI machine output, diagnostic readers.

* Add typed wrapper bindings for the reviewed Actions plan/run/watch/pipeline operations with bounded declared input/branch/target schemas, not arbitrary shell passthrough. Remote execution requires the same source and confirmation gate as the direct CLI.
* Add safe `--resume-if-existing` *opt-in* or an equivalent explicit inspect/resume route, without another POST when request identity is uncertain. Show exact GitHub attempt and retained receipt if available.
* Provide compact `actions summary/diagnose` and repo-scoped `devtool capabilities` with installed-versus-configured-versus-eligible, native/hosted toolchain and blocked authority distinctions. Design for 40–60-column Termux output, JSON parity and no secret disclosure.
* Standardize error code, affected target/path, safe next command and recovery receipt; detect `.devtool` generated state in clean worktree preflight without broadly suppressing real changes.

**Gate:** non-interactive JSON and 40-column interactive acceptance; wrapper cannot execute undeclared remote action; dispatch-once and secret-safe negative fixtures; same error identity appears in CLI, machine API and operator UI; no release/device state fabricated.

### NEXT-11 — Full cross-consumer qualification (separate owner authority, final)

**IDs:** independent R08/R09 requirements; all phases' qualification statuses. **Owners:** `rmp_r08_assessment.py`, `rmp_r09_campaign.py`, applicable source/CI/device/consumer owners, independent release authority.

* Do not treat the R09 self-report, local pytest or third-party JSON claims as hosted/device/formal security/release authority. Collect independently verified current-source CI24 24 shards, G3R native 13 lanes, FS03, A09 second Go consumer, matching Actions run/attempt/artifact; native Termux installed-wheel/SSH return evidence; actual Skybind/Cinderbox/BKX/chroot-ctl and configured consumer checks; Android instrumentation, installed pair, trusted broker identity, external worker and notification delivery.
* Keep R08 formal N01, N02, C04, C05, C06, Q01, Q02, U01 and original ENV-A06/ANDROID-A16/DIST-A01/DIST-A12/DIST-A13/TEST-A11 independently open until their actual authorities sign off.
* Run focused negative tests per patch, complete current-source source tests/installed-wheel tests before release candidate, hosted matrix only when source authority matches, and device-native lanes only when an independently usable test substrate is available.

**Gate:** R09 reports all seven dimensions independently with inspectable exact evidence. Source implementation complete is allowed while `formal_release_qualified=false`; no unconditional green.

## 5. One-to-one backlog traceability

This is the **primary implementation phase** for each issue (dependencies can reference the same issue elsewhere); historical descriptions remain in the cumulative backlog.

| Phase | Stable IDs |
|---|---|
| Already implemented, regression only | DT-009, DT-012, DT-019 |
| NEXT-00 | DT-013, DT-014, DT-028, DT-056 |
| NEXT-01 | DT-020, DT-029, DT-030 |
| NEXT-02 | DT-006, DT-010, DT-017, DT-023, DT-051, DT-052, DT-054 |
| NEXT-03 | DT-035, DT-041, DT-042, DT-053, DT-055 |
| NEXT-04 | DT-031, DT-033, DT-043, DT-044 |
| NEXT-05 | DT-004, DT-008, DT-011, DT-021, DT-024, DT-025, DT-026, DT-027, DT-032, DT-034 |
| NEXT-06 | DT-037, DT-038, DT-039, DT-040 |
| NEXT-07 | DT-036, DT-045 |
| NEXT-08 | DT-046, DT-047, DT-048, DT-049, DT-050 |
| NEXT-09 | DT-002, DT-003, DT-005 |
| NEXT-10 | DT-001, DT-007, DT-015, DT-016, DT-018, DT-022 |

## 6. Minimal implementation and acceptance artifact for each batch

For each coherent root-cause batch: (a) current HEAD and source preimage manifest; (b) reviewed source diff; (c) unit/contract/negative tests and a disposable Git acceptance; (d) generated documentation and additive backlog/index delta; (e) single self-contained source-bound overlay ZIP with automatic scoped commit only if installation is explicitly requested; (f) machine JSON pass/fail/blocked receipt plus exact Git source and run IDs; (g) Termux-friendly one-command install using local `~/Downloads` bundle only when user authorizes application; (h) separate hosted/native/device acceptance receipts without extrapolation. Never stack stale overlays or touch another checkout implicitly.

## 7. Recommended first implementation batch

**`NEXT-00 + NEXT-01`**: repair the canonical index/docs, characterize ZIP and import behavior, then implement historical `transaction retire --preserve-current` with adversarial Git tests. This is the most important safety prerequisite before large-scale automatic multi-repo migration. **NEXT-02 typed evidence schema design** can proceed simultaneously without any remote dispatch; then build NEXT-03 and its Go/Python consumers. Run NEXT-09 Android static/variant improvements as an independent short source-bound branch. Do not hold useful Devtool feature development hostage to unrelated independent H1/H2 authority, and do not report those authorities as closed.

## 8. Audit references and verification limitations

- Source: `devtool/wrapper_config.py:52,364+`, `actions_android.py:78–127,173–205,453–470`, `actions_adoption.py:61–143`, `actions_polyglot.py`, `actions_lineage.py:235+`, `transactions.py:1672+`, `overlay/recovery.py`, `workflow_plan_views.py`, `workflow_scheduler.py`, `federation_dag_contract.py`, `rmp_r08_assessment.py`, `rmp_r09_campaign.py`.
- Docs: `docs/DEVTOOL_IMPLEMENTATION_ROADMAP.md` (R0–R9), `docs/DEVTOOL_IMPROVEMENTS.md`, `docs/DEVTOOL_IMPROVEMENT_INDEX.json`, `docs/actions/DEVTOOL_R08_SECURITY_AUTHORITY_MATRIX_20261021.json`, `docs/actions/DEVTOOL_R09_ACCEPTANCE_CONTRACT_20261021.json`, `docs/actions-06-pipeline.md`, `.gitignore`.
- Tests (local, no network): `test_workflow_r06_plan_views`, `test_actions_a03_adoption`, `test_actions_a09_ci_host_contract`, `test_actions_g05_polyglot_ci`, `test_actions_r04_lineage`, `test_overlay_r06d_recovery_plan`, `test_actions_05_android_builds`, `test_actions_06_pipeline_dag`, `test_wrapper_hardening_hotfix`, `test_actions_r04b_lineage_authority`, `test_actions_r05_operator`, `test_rmp_r08_assessment`, `test_rmp_r09_campaign`: **139 PASS, 4 SKIP** (Textual missing).
- All cross-repo suggested acceptance consumers require their own up-to-date source snapshots and independent provider evidence. No changes were made to GitHub or users' active checkouts.
