# R06C — Child-owned producer → consumer DAG contracts

## Read-only operator projection

`devtool federation dag-contract SHIP --json` projects the **existing FRX**
parent workflow and child-owned target/workflow contracts into a deterministic,
source-bound producer/consumer graph. The command never invokes a child, writes a
lock, changes a repository, clones/fetches, or queries GitHub. A compact 40-column
fallback is printed without `--json`.

`devtool federation dag-contract SHIP --require-frozen --json` requires a
**previously persisted**, **matching** strict-local FRX source lock. A matching
lock establishes *local source and graph identity only*: no hosted verification,
artifact bytes, RUN-V3 evidence, Android test or release qualification.
`devtool federation plan SHIP --freeze` is a **separate explicit operation**.

The report includes full child-owned machine IDs, repository/target and public
contract refs, declared input artifact requirements, cross-repository edges,
execution-environment declarations, canonical FRX graph/source-lock digests,
and a stable SHA-256 fingerprint of the contract (not a signature). The graph
is built solely via `build_federated_plan` / `build_federated_group_plan`; it
never creates another scheduler. Capability-provided dependencies remain real
FRX dependencies; serialization due only to resource reservations is **not**
misrepresented as a declared graph edge.

## Minimal parent-owned enrollment example

Each child's `.devtool.toml` owns its target and workflow. The coordinator
requires only explicit enrollment plus workflow references. For example:

```toml
[project]
runner = "command"
[federation]
schema = 1
[federation.repositories.skybind]
path = "../skybind"
[federation.repositories.cinderbox]
path = "../Cinderbox-Companion"
[federation.repositories.bkx]
path = "../bkx"
[federation.repositories.chrootctl]
path = "../chroot-ctl"
[federation.repositories.studyflow]
path = "../Studyflow"

[federation.workflows.integration]
steps = [
  { id="skybind-check", ref="skybind::android::validate" },
  { id="cinderbox-check", ref="cinderbox::android::validate", depends_on=["skybind-check"] },
]
```

**Illustrative names only:** actual child target/workflow names and paths must be
confirmed against real repository configurations. The examples do not claim
that BKX, Cinderbox, Skybind, chrootctl or Studyflow have these exact workflow
names, are enrolled, run successfully, or qualify a release. The checked-in
R06C tests use synthetic local repositories, not live consumer snapshots.

For source review, first use `devtool federation plan integration --json`.
After reviewing the plan, an operator may choose to freeze the exact local
sources; `dag-contract --require-frozen` then checks matching identity. Drift
produces a refusal rather than a guessed rebase or silent source replacement.

## Source evidence vs external acceptance

New projection tests exercise explicit producer→consumer dependencies, typed
artifact declarations without pretending a registry transfer occurred, unknown
or drifting frozen locks, deterministic digests, the canonical parser and
unaltered parent config. Existing FRX source lock, capability/schedule,
interface, ARTIFACT-Y08 and CORE-X04 tests remain applicable. Real consumer
configuration review and producer→consumer execution receipts are **open**.
No new hosted, device, signing, or release authority is introduced.
