# Native Overlay Delivery v3 — M0 invariant-to-test/CI matrix

**Source:** `Mikeyphw/devtool@8c5ab9bf050f9aaba5d181d0a6819a8e1e0b28a9` (tree `740cc6d1127626ed174595ce2de2d74ddd1b5c5c`). This is a *source-reviewed coverage inventory*, not executed test results. The named tests exist in the verified tree and their relevant declarations/source were inspected. Exact local/hosted/device pass evidence belongs to the M0 resulting commit and must be recorded separately.

| ID | Mandatory invariant / refusal | Maintained current checks | CI / acceptance owner | M0 disposition |
|---|---|---|---|---|
| I01 | Clean named target branch; correct GitHub origin; preapply HEAD/tree | `tests/test_launcher_contract_v3.py`; `tests/test_delivery_adoption.py`; `tests/test_g0a_native_delivery_coordinator.py` | Native preflight; `devtool-launcher-v3.yml`, S7 P02 | Retain; native runtime proof required |
| I02 | Runtime origin, exact commit/tree, launcher module blob and CLI schema blob; 3.x ABI | `tests/test_launcher_runtime_compat.py`; `tests/test_g0c3_launcher_compatibility.py`; `tests/test_launcher_contract_v3.py` | Launcher workflow / native attestation | Retain; native M2 integration later |
| I03 | Pinned target and independently verified detached runtime | `tests/test_g0c3_launcher_compatibility.py`; `tests/test_launcher_contract_v3.py` | Runtime source contract / launcher workflow | Retain |
| I04 | Archive metadata, manifest, full inventory, payload sizes/digests and source blob hashes | `tests/test_g0a_executable_overlay.py`; `tests/test_artifact_v2.py`; `tests/test_artifact_validator_payload_policy.py`; `tests/test_fc04_artifact_integrity.py` | Native `validate-overlay` and `overlay check` | Retain |
| I05 | Invalid schema/capabilities are refused; proposed v3 is not silently accepted | `tests/test_g0a_executable_overlay.py`; `tests/test_launcher_validation_delivery.py`; `tests/test_artifact_validation_fail_closed_hotfix.py` | Native artifact parser; new M1 cases required | Existing schema ≤2; new v3 negatives pending M1 |
| I06 | Unknown PYZ never self-verifies by running its `__main__`; canonical executable profile | `tests/test_g0a_executable_overlay.py`; `tests/test_launcher_validation_delivery.py` | Native verifier from trusted runtime; runtime smoke | Retain; new pre-first-execution M2 gate |
| I07 | No symlink, traversal, archive aliasing, duplicate ZIP names, bomb/decompression or inventory collision | `tests/test_g0a_executable_overlay.py`; `tests/test_artifact_validator_payload_policy.py`; `tests/test_artifact_v2.py` | Native artifact validation and review | Retain all negatives |
| I08 | Immutable v1/v2 bootstrap; legacy launcher profiles never silently repinned | `tests/test_g0a_executable_overlay.py`; `tests/test_g0c3_launcher_compatibility.py`; `tests/test_launcher_runtime_compat.py` | Launcher workflow; negative fixture | Retain historical fixtures as security contract |
| I09 | Required executable validation must have real, unskipped step evidence | `tests/test_g0a_native_delivery_coordinator.py`; `tests/test_launcher_validation_delivery.py`; `tests/test_validation_manifest_skip_contract.py` | Native runner and delivery-history receipt | Retain; validate actual receipt |
| I10 | Exclusive `validation.tasks`; disabled built-in phases not reenabled by CLI | `tests/test_validation_manifest_skip_contract.py`; `tests/test_g0a_native_delivery_coordinator.py` | Native validation selector | Retain merged PR #29 behavior |
| I11 | Atomic apply, rollback, reservations, no untracked mutation/reapply | `tests/test_transactions.py`; `tests/test_ov01_transactional_safety.py`; `tests/test_fc02_transaction_closure.py` | Native transaction history; full host checks on request | Retain; M3 fault-injection additions pending |
| I12 | Postcommit qualified receipt: exact parent/tree, changed paths, digest and transaction IDs | `tests/test_g0a_native_delivery_coordinator.py`; `tests/test_delivery_session.py` | Native adoption / immutable history | Retain; ZIP automatic integration pending M3 |
| I13 | One durable session; refusal on ambiguous/dirty/drifted or corrupt journals | `tests/test_g0a_native_delivery_coordinator.py`; `tests/test_delivery_session.py`; `tests/test_delivery_adoption.py` | Existing delivery session journal | Retain |
| I14 | After commit only status/resume/deliver/CI; no second apply or rollback of committed state | `tests/test_g0a_native_delivery_coordinator.py`; `tests/test_delivery_session.py` | Native `adopt-committed` and `resume` | Retain; M3 crash-recovery parity pending |
| I15 | Push needs explicit approval and proven fast-forward; never force | `tests/test_delivery_github.py`; `tests/test_delivery_ci_policy.py`; `tests/test_g0b_delivery_fork_and_provider.py` | Delivery GitHub native journal | Retain |
| I16 | Exact PR head/base/fork identity and draft default; POST loss never retried | `tests/test_g0b_delivery_fork_and_provider.py`; `tests/test_delivery_github.py` | PR provider, actual server observation | Retain |
| I17 | Committed CI policy, numeric required workflow ID, correct event/head/run attempt and missing/skipped refusal | `tests/test_delivery_ci_policy.py`; `tests/test_delivery_ci_registrar.py`; `tests/test_delivery_ci_window_pagination.py` | Native CI registrar and required hosted runs | Retain; must inspect workflow applicability |
| I18 | Fork upstream policy/base pin is distinct from local source HEAD | `tests/test_g0b_delivery_fork_and_provider.py`; `tests/test_delivery_ci_policy.py` | Upstream policy/branch verification | Retain |
| I19 | Full 24-shard Python regression distinct from required PR CI and release | `tests/test_g0b_delivery_fork_and_provider.py`; `tests/test_delivery_cross_consumer.py` | Manual `devtool-full-repository-pytest.yml` | Explicit request only; NOT RUN in M0 |
| I20 | Native terminal output, no automatic clipboard, no full-screen Textual host | `tests/test_g0c3_launcher_compatibility.py`; `tests/test_launcher_contract_v3.py`; `tests/test_clipboard.py` | CLI/native TTY/Termux check | Retain; no simulated output |
| I21 | Native CLI/parser and generated schema/completion parity | `tests/test_delivery_cli_surface.py`; `tests/test_core_x04_generated_surfaces.py`; `tests/test_completion_and_manpage.py` | S7 P02 and current generated surface gates | Retain; v3 commands not yet declared |
| I22 | Durable XDG session/writer authority; no transaction source pollution | `tests/test_storage_layout.py`; `tests/test_storage_layout_integration.py`; `tests/test_validation_sessions.py` | Native XDG/Termux, PR #31 pending | Retain; re-review PR #31 before M1 |
| I23 | No missing required PR workflow may be reported as qualified success | `tests/test_delivery_ci_registrar.py`; `tests/test_delivery_ci_policy.py`; `tests/test_delivery_ci_source_recheck.py` | Actual GitHub checks/rulesets at PR HEAD | Retain; missing run ≠ pass |
| I24 | Android/device and release acceptance never inferred from Python/PR CI | `tests/test_delivery_ci_policy.py`; `tests/test_g0b_delivery_fork_and_provider.py`; `tests/test_roadmap_delivery_audit.py` | Dedicated device/release pipelines | Retain; NOT PERFORMED in M0 |

## Execution matrix and non-equivalent evidence

| Lane | Command or real source | Intended evidence | M0 state at preparation |
|---|---|---|---|
| Source-only static review | GitHub tree/commit + source/fixture inspection | Component/source inventory and test mapping | Inspected; no execution |
| Bounded native local/Termux regression | Native manifest `validation.tests` with the current G0A coordinator, G0A executable, G0C3 compatibility, validation-phase tests | Executed outcomes and transaction receipt | Pending actual native apply |
| Preapply archive verification | Trusted runtime `validate-overlay`, `overlay check`, exact payload inventory | Source/origin/branch/artifact proof | Pending generated package and native validation |
| Native apply/commit/adoption | Canonical launcher -> native transaction and session | Resulting commit/tree, history ID, transaction and durable session | Pending target runtime |
| Hosted focused PR | Existing `devtool-delivery-s7-p02.yml` + applicable/required workflow runs | Actual PR HEAD, attempt, event, job conclusion | Pending pushed PR; no hosted pass claimed |
| Full repo suite | `.github/workflows/devtool-full-repository-pytest.yml` 24-shard dispatch | Independent 24-shard + summary correlation | Not requested / not run |
| Device/release | Termux smoke/Android/release-specific owner | Device/release evidence | Not performed |

## Coverage retirement, discovery and CI gaps

- **Changed checks:** none for M0; the native builder's existing `validation.tests` selects maintained live current tests instead of adding a phase-specific source-assertion test.
- **Deleted or bypassed checks:** none. `G0`, `S7`, `HFxx` and similar historical labels are not proof of obsolete behavior. CI cannot be reduced without verifying equivalent hosted negative/provider coverage and branch/ruleset usage.
- **Current CI path-filter risk:** `.github/workflows/devtool-launcher-v3.yml` does not list these new M0 documentation files in its reviewed `pull_request.paths` selection; the job may not start on a docs-only PR. The unfiltered S7 P02 PR workflow is a separate current test surface, not necessarily formal required-CI qualification.
- **Current required-CI risk:** the committed `config/delivery-required-ci.json` lists six named .NET source/acceptance workflows. They must be checked against the *actual M0 PR* and branch policy; absence, skipped, out-of-scope or stale runs are **NOT QUALIFIED**, not silently passed.
- **Open PR #31:** targeted-default validation, validation storage and expanded launcher workflow filters are prospective. Review its exact merged SHA and current test evidence, then update this inventory if it lands. Never incorporate an unmerged change into the M0 source pin.

**Acceptance rule:** Each invariant above has at least one currently maintained relevant test; this is *coverage inventory only*. Passing targeted local tests, native receipts, PR hosted checks, on-demand full regression and device/release evidence remain separate qualification claims. Future M1–M5 changes must refresh this matrix at their exact new base and preserve equivalent security-negative coverage.
