# REM01 whole-scope fixed-point audit hotfix

Roadmap position: REM01 hotfix after REMED-R01A/R01B, before REMED-R02A.  The hotfix does not add a new implementation position; it repairs REM01 production-truth gaps found by the post-R01A+R01B fixed-point audit.

## Canonical scope inspected

Current REM01 scope is the union of REMED-R01A and REMED-R01B against the merged M001-M008 remediation campaign, later amendments, inherited source promises, preserved historical findings, and regression contracts.

Authoritative promise maxima used by this audit:

- CORE-P084
- EXEC-P087
- ENV-P131
- ANDROID-P111
- LIFE-P127

The earlier R01B report/disposition recorded `ANDROID-P099`; this hotfix corrects that paperwork to `ANDROID-P111` because the preserved merged handoff contains Android promises through P111. Historical device run logs are not rewritten.

## Fixed-point audit result before hotfix

REM01 was not fully delivered as production truth. The audit found five reopened findings:

1. **CORE-A01** — default `devtool completion zsh` still used the legacy handwritten Zsh body unless `--canonical` was supplied. That left a runtime surface outside the canonical CORE schema.
2. **CORE-A02** — the legacy completion body still exposed alias-bearing command authority as the default completion surface.
3. **CORE-A08** — ambient `DEVTOOL_FEDERATION_*` variables could still manufacture federation lineage in invocation metadata without a trusted bridge or parent-bound capability.
4. **LIFE-A03** — Android signing mode `devtool` could synthesize an ephemeral LIFE-shaped identity from Android-local keystore/alias/certificate fields when no `identity_id` was configured. That made legacy Android signing config a reverse authority.
5. **LIFE-A05** — artifact execution records were persisted immediately after mutation with success-shaped RUN status before validation/rollback/finalization, and record/capture identity did not reliably bind the canonical primary repository tree.

## Production changes made

- `devtool completion zsh` now emits the generated CORE surface by default. The handwritten Zsh body remains only a compatibility constant and is no longer the normal runtime authority.
- `mode = "devtool"` Android signing now requires a registered LIFE `identity_id`. Project-local/Gradle signing remains outside this devtool-managed identity authority; devtool-managed signing can no longer derive identity from legacy Android fields.
- Federation lineage now requires either trusted in-process federation context or a private, immediate-parent, owner/mode checked lineage handoff file. Public ambient `DEVTOOL_FEDERATION_*` variables alone are no longer invocation lineage authority.
- Federation child launchers create that private lineage handoff while leaving public federation variables as provider-routing hints only.
- Artifact execution evidence now records `execution_status`. A mutation-only apply result with an open transaction is `applied-pending-validation`, not `succeeded`.
- Artifact execution records and attachments are persisted under the canonical primary repository RUN-V3 catalog when transaction metadata identifies a primary root.
- Artifact final-state identity now includes a concrete terminal tree identity (`git-tree:<tree>` when available, otherwise `tree-sha256:<digest>`), not only plausible transaction metadata.

## Validation

Targeted fixed-point validation on the construction host:

```text
51 passed
0 failed
```

Command:

```bash
python -m pytest -q --tb=short \
  tests/test_remed_r01_whole_audit_hotfix.py \
  tests/test_remed_r01a_canonical_authority_identity.py \
  tests/test_remed_r01b_trust_resolution_mutation.py \
  tests/test_core_x04_generated_surfaces.py
```

The native wrapper workflow is present as `./devtoolw rem01-hotfix`. On the construction host it correctly refuses because the workflow is declared `native-termux`; the device artifact validation must run it transactionally.

## Qualification state after hotfix

- CORE-A01: IMPLEMENTED_AND_PRODUCTION_ADOPTED for REM01-owned parser/completion authority.
- CORE-A02: IMPLEMENTED_AND_PRODUCTION_ADOPTED for REM01-owned default completion surface; legacy body is compatibility-only.
- CORE-A08: IMPLEMENTED_AND_PRODUCTION_ADOPTED for REM01-owned wrapper/federation invocation provenance.
- LIFE-A03: IMPLEMENTED_AND_PRODUCTION_ADOPTED for Android devtool signing identity authority.
- LIFE-A05: IMPLEMENTED_AND_PRODUCTION_ADOPTED for REM01-owned artifact execution record truth; broader destructive cleanup remains R03B-owned.

Remaining non-REM01 boundaries are unchanged and stay owned by later remediation overlays and G3R gates.
