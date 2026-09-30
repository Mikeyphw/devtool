# LIFE-X03 Generated Ownership and Cleanup Convergence

Roadmap position: **43/70**  
Gate window: **LIFE ownership window 1/2**  
Next gate: **LIFE-G2 44/70**  
Next major scope: **ANDROID-X03 45/70**

## Purpose

LIFE-X03 replaces filename/age/ignore-rule cleanup authority with canonical, reconstructable ownership. Cleanup discovery may still use names, suffixes, ignore state, or age as hints, but deletion requires a generated-output descriptor, a fresh protection/reference view, and plan revalidation immediately before mutation.

## Canonical model

The implementation adds `devtool.cleanup_lifecycle` with:

- `GeneratedOutputDescriptor`
- `CleanupReference`
- `CleanupOwnershipView`
- `CleanupCandidate`
- `CleanupPlan`
- `CleanupReceipt`
- `OpaqueCleanupAction`
- cleanup safety classes and explicit scopes
- repository-cache relocation planning
- destination-qualified remote staging descriptors
- canonical candidate explain projection

The ownership view consumes canonical RUN attachments/streams, RER/reuse metadata, seal metadata, produced-artifact registry records, active artifact transactions, and active validation sessions. RUN keeps attachment/tombstone authority; transaction worktrees remain transaction-owned; artifact identity remains artifact-registry-owned.

## Safety convergence

### Python

Python cleanup no longer deletes generic `build`/`dist` merely by name. Provider-known caches/environments are explicit generated outputs; heuristic artifact shapes require corroborating ownership such as Git-ignore state. Tracked content is protected unless an owner explicitly authorizes tracked deletion.

### Backups/history

Backup-shaped names are advisory only. Known historical Devtool top-level backup directories remain explicit legacy-owned archival material, but generic `.bak`/`.orig`/`.before` paths require ownership. Retention age selects candidates only; fresh ownership/reference/drift checks authorize deletion.

### Go

Project clean is explicitly `go clean ./...` with `global_cache_mutation=false`; it never silently adds `-cache`, `-testcache`, or `-modcache`. Process-wide Go cache GC is a separate global cleanup scope.

### Provider clean actions

Opaque provider commands such as Node/package-manager clean actions are typed `OpaqueCleanupAction`s. Devtool does not fabricate path previews for opaque provider behavior. Provider results are projected into `CleanupReceipt` after execution rather than re-executed by the cleanup model.

### Repository/global/remote scope

Repository caches are repository-key/backend scoped. Global cleanup requires explicit global authorization and confirmation metadata. Remote staging is destination-qualified and cannot be applied controller-side without a destination-local provider executor.

## Revalidation and protection roots

Before deletion, the plan revalidates:

- repository identity
- policy generation
- destination
- Git/tracked state
- protection/reference roots
- path existence/type/size/mtime/content digest
- directory tree content digest
- filesystem identity/byte accounting

Protection roots include RUN attachments and streams, RER reusable outputs, seal paths/pins, produced artifacts, active transactions/recovery state, and active/resumable validation state. Published artifacts stay outside ordinary automatic cleanup.

## Receipts and provenance

Every applied/protected/failed cleanup action is represented in `CleanupReceipt`, including reclaimed bytes, reasons, postconditions, invalidated evidence IDs, and tombstone provenance. Receipts can be persisted under private Devtool cleanup state and linked back into the canonical RUN catalog as private attachments. RUN remains the tombstone authority.

## CLI/provider adoption

Top-level `devtool clean` now annotates provider cleanup specs with canonical scope/destination semantics, builds an opaque cleanup plan, executes through the existing provider runner, and projects results into a RUN-linkable cleanup receipt. Cache/toolchain cleanup results carry explicit cleanup scope/destination metadata instead of being semantically indistinguishable from project clean.

## Validation

Devtool-native construction workflow:

- `./devtoolw life-x03`
- **92 passed, 0 failed, 0 skipped**
- **5/5 stages**
- **0 warnings, 0 errors**

Additional retained transaction/RUN/RER/validation authority regression:

- **97 passed, 0 failed**

The production artifact keeps `targets.devtool.execution_environment = "native-termux"`; the construction workflow used a temporary host-only override that was restored before packaging.

### Native-Termux remediation

The first native-Termux qualification attempt (`1f527d4a60a54d2cb9b3f73118136b44`) reached the retained CLI cleanup stage and exposed a host-dependent fixture in `CliTests.test_clean_caches_does_not_implicitly_run_project_clean`: the test expected `desktop-linux` while leaving execution environment selection on `auto`, which correctly resolves to `host-android` on Termux. Production cleanup/backend resolution was unchanged. The retained test now explicitly requests `--execution-environment desktop-linux`, making its assertion hermetic across hosts. The full 92-test LIFE-X03 workflow was then rerun successfully.

## Exported obligations

LIFE-G2 remains responsible for adversarial gate proof that no heuristic-only deletion path survives, global/repository policy cannot weaken trust, active protection roots win under races/drift, and cleanup failure cannot replace an earlier primary lifecycle failure.

LIFE-X04/presentation work remains responsible for user-facing plan/apply/status/doctor/explain rendering while consuming these X03 models without changing semantics.
