# GIT-FRX-09 — Parent/Child Git Lineage

## Outcome

GIT-FRX-09 makes Git lineage a first-class part of every federated child execution while preserving the existing authority boundaries:

- the GIT-FRX-08 / FRX-Y03 frozen source lock remains the authority for planned source identity;
- each child EXO run remains authoritative for its own execution state;
- the parent federation report coordinates and preserves child lineage without becoming a second child run store;
- Devtool private refs provide inspectable result/recovery anchors without moving user branches.

The milestone is non-gate work and is intended to apply with `--no-validate`. It has no finalize/reinstall hook.

## Two-phase lineage model

A child Git lineage record has one schema and two lifecycle phases.

### Planned lineage

Before child execution, Devtool derives planned lineage from the frozen federation source lock and typed FRX-Y07 contract identity. The child RunStarted lineage receives:

- parent federation run ID;
- parent plan node ID;
- child run ID;
- federation workflow/step;
- repository name and stable Git repository ID when applicable;
- target;
- contract type/name/ref (`workflow`, `job`, or `wrapper`);
- frozen plan lock digest and equivalence digest;
- frozen repository source identity;
- start commit or explicit unborn state;
- planned branch/head ref;
- expected private result ref;
- commit mode.

Result state is deliberately not invented at child start.

### Finalized lineage

After the child reaches a terminal state, the same lineage is finalized with observed evidence:

- terminal status;
- actual result commit/HEAD when Git-backed;
- transaction commit policy and result commit;
- superseded/amended commit pairs;
- overlay/artifact digests associated with the child run;
- transaction finalization/recovery state;
- Devtool private `result` ref;
- paired private `rollback` ref anchored at the frozen start commit.

The record is persisted under Devtool state storage and published back to the child EXO run as a verified `federation-git-lineage` artifact. The parent NodeFinished metadata and `FederatedChildRun` summary store the same finalized record.

## Private result and rollback refs

For a Git-backed child, GIT-FRX-09 materializes only Devtool-private refs. The planned result-ref prefix from the frozen lock is authoritative; the paired rollback ref is created under the same prefix.

- `.../result` points at the terminal child result commit/HEAD;
- `.../rollback` points at the frozen child start commit.

Existing refs are reused only when they already point at the required object. A conflicting existing ref fails closed. No user branch is moved and no push is performed.

## Transaction and artifact evidence convergence

The finalizer consumes existing authorities rather than creating a second commit/overlay ledger:

- transaction history supplies commit mode, result commit, superseded commit and finalization/recovery state;
- artifact operation history supplies overlay/artifact SHA digests for the child run;
- the frozen source lock supplies planned source and expected-result identity;
- EXO remains execution truth.

## Cancellation and failure durability

Terminal failure and exit 130 preserve finalized lineage just like success. Explicit interruption paths attempt normal lineage finalization before the parent unwinds.

If lineage finalization itself fails while processing cancellation—for example because a private ref conflicts—Devtool persists a `finalization-error` lineage artifact containing the cancellation state and finalization error. This prevents the failure of a recovery/evidence operation from erasing the child lineage that explains what happened.

The parent report therefore keeps inspectable child Git lineage after success, ordinary failure, cancellation, or lineage-finalization failure.

## Non-Git children

Explicitly enrolled non-Git repositories keep logical federation lineage (parent/node/repository/target/contract/frozen-plan identity where available) without fabricating repository IDs, commits, or refs.

## Path independence

Portable lineage does not include the local child repository root. Frozen-plan and repository source identities are carried by digest/ID, allowing later SSH and remote-attestation work to consume lineage without treating a host path as source identity.

## Native roadmap execution

Target: `devtool`

Workflow: `roadmap-git-frx09`

Declared test: `roadmap-git-frx09`

Target-local non-test job: `federation-git-lineage-fixture`

Workflow DAG:

1. `job:source-syntax`
2. `job:federation-git-lineage-fixture`
3. `test:roadmap-git-frx08-freeze`
4. `test:roadmap-git-frx09`
5. `test:roadmap-workflow-exo-baseline`

Native plan evidence reports 5 nodes / 5 waves / 2 target-local jobs. Analysis-host preflight fails only because the target requires `native-termux` while the construction host is `desktop-linux`.

## Validation evidence

Focused and bounded audit results before packaging:

- existing FRX-Y07 + FRX-Y01 + frozen-source compatibility: **44/44 passed**;
- GIT-FRX-01 through GIT-FRX-09: **131/131 passed**;
- federation FRX01..FRX11: **100/100 passed**;
- FRX12 + full reseal + FRX-X01..X04: **62/62 passed**;
- FRX-Y01 + FRX-Y04..Y07: **55/55 passed**;
- total bounded federation regression: **217/217 passed**;
- Gate-1 regression aggregate: **103/103 passed**;
- declared `roadmap-git-frx09`: **9/9 passed** with JUnit;
- native roadmap execution contract: **4/4 passed**;
- post-cancellation-hardening GIT-FRX-09 + FRX-Y07: **18/18 passed**;
- Python compileall: **PASS**.

The GIT-FRX-09 focused contract covers planned lineage, child RunStarted propagation, real Git result commits, private result/rollback refs, transaction/overlay/supersession folding, cancellation preservation, ref-collision fail-closed behavior, non-Git children, parent report serialization, and path independence.

## Roadmap handoff

With GIT-FRX-09 complete, the integrated roadmap deliberately pauses the federation sequence and begins the three-overlay SSH campaign:

1. SSH-01 — Transport, Environment & Worker Preparation
2. SSH-02 — Execution, History & Recovery Convergence (SSH milestone gate)
3. SSH-03 — Lifecycle Hardening & Final Qualification (SSH final gate)

FRX-Y02 resumes afterward and extends the canonical remote run/recovery substrate created by SSH-02 rather than introducing a second remote recovery authority.
