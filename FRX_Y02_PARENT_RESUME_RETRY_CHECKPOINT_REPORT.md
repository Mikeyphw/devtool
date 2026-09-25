# FRX-Y02 Parent Resume / Retry / Checkpoint Report

## Scope

FRX-Y02 adds dependency-aware parent federation resume and retry without creating a second execution-history authority. A federation parent run now persists a compact checkpoint under Devtool state. The checkpoint stores the parent DAG identity, child result references, and reuse predicates needed to decide whether each previously successful child is still reusable.

Public CLI:

- `devtool federation resume <run>`
- `devtool federation retry <run>`
- `devtool federation retry <run> --failed`

The resumed execution uses the existing federation execution engine and EXO child runs. For remote children it references the canonical SSH-02/SSH-03 run/history/recovery evidence; it does not copy SSH stdout, stderr, tmux state, return-transfer state, leases, or worker history into the federation checkpoint.

## Reuse contract

A successful child is reusable only when all applicable predicates still hold:

1. parent structural plan identity is unchanged;
2. repository identity is unchanged;
3. frozen Git/tree/config source identity is unchanged;
4. target/workflow/public-contract identity is unchanged;
5. upstream child result identities are unchanged;
6. relevant capability/interface identity is unchanged;
7. required artifact input identities are unchanged;
8. the prior child is terminal-success and its referenced evidence is still available.

A parent DAG shape change fails closed and requires a new plan/run. Child-level drift does not silently broaden reuse: the changed child becomes `rerun_due_to_drift`, and dependent children are invalidated when their upstream result or artifact-input identity can no longer be proven equal. `retry --failed` fails closed if a previously successful child has become invalid rather than quietly rerunning it under a failed-only request.

## Source identity and Devtool runtime state

FRX-Y02 deliberately excludes only Devtool-owned `.devtool/**` runtime files from the source dirty predicate. This prevents a normal child run from self-invalidating merely because it created Devtool runtime state such as the artifact registry. Git HEAD/tree/index/config identity and user/source dirty paths remain part of the predicate, so real source drift still invalidates reuse.

## Parent lifecycle truth

Federation reports expose lifecycle states independently from child terminal status:

- `reused`
- `retried`
- `rerun_due_to_drift`
- `blocked`
- `failed`
- `cancelled`

The parent report includes lifecycle counts and the resume/retry mode. Reused children keep their canonical prior child run ID and evidence references and are not re-entered.

## Native Devtool adoption

FRX-Y02 is represented by first-class Devtool contracts:

- target-local job: `federation-resume-invalidation-fixture`
- test: `roadmap-frx-y02`
- workflow: `roadmap-frx-y02`
- `roadmap-current` extends through FRX-Y02

The fixture proves both halves of the contract: Devtool-owned runtime state does not invalidate an unchanged child, while a real upstream Git commit invalidates that child and its dependent.

## Validation evidence

Final audit evidence:

- focused FRX-Y02 + native-roadmap contracts: 13 passed;
- federation regression batches plus GIT-FRX09: 235 passed;
- SSH-02/SSH-03 focused boundary contracts: 38 passed;
- native `roadmap-frx-y02`: 7/7 stages, 106/106 tests, 0 warnings, 0 errors.

The native fixture output is intentionally compact and Git-backed fixture subprocesses are bounded so self-hosted qualification fails closed instead of hanging indefinitely.

FRX-Y02 is an interim federation milestone, not a full federation gate. Its overlay is therefore intended to apply with `--no-validate`; construction and replay validation are performed before handoff.

## Next milestone

The next roadmap milestone is FRX-Y08, explicit artifact producer → consumer edges. It will consume the child result/artifact identity groundwork here without changing FRX-Y02's checkpoint or SSH authority boundaries.
