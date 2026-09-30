# LIFE-G2 — Ownership / Retention Milestone Gate

Roadmap position: **44/70**  
LIFE gate window: **2/2**  
Previous implementation: **LIFE-X03 43/70**  
Next implementation: **ANDROID-X03 45/70**

## Purpose

LIFE-G2 is the authoritative adversarial gate for generated-output ownership and
reference-aware retention. It audits the LIFE-X03 model under races and hostile
inputs rather than merely rerunning its happy-path tests.

The gate criteria are:

```text
tracked user/source names survive
heuristic-only candidates stay protected
global caches require explicit global scope
active RUN/RER/transaction/validation/seal refs survive
stale CleanupPlan cannot delete changed resources
partial cleanup remains partial
remote/local destinations cannot be confused
```

No age, suffix, directory name, Git-ignore match, or presentation/config hint is
deletion authority by itself.

## Gate audit findings folded into G2

The audit found four gate-owned correctness gaps and one stale retained test
contract. All were fixed before the gate was packaged.

### 1. Apply-time ownership was stale

`apply_cleanup_plan()` previously recompiled from the ownership view passed in by
the planner. A RUN attachment, active transaction, or active validation session
that appeared after planning could therefore be invisible at mutation time.

G2 adds `refresh_cleanup_ownership_view()`. Apply now re-projects canonical live
RUN/RER, artifact, transaction and validation authorities immediately before any
mutation. Caller references remain conservative inputs and newly-active roots
win over the stale plan.

### 2. Remote cleanup could touch the controller filesystem

A non-local `CleanupPlan` required a provider executor, but path candidates still
flowed through controller-side `Path`/`shutil` deletion. G2 separates remote path
mutation from opaque provider actions. Remote candidates now require a
destination-local `destination_executor` returning a typed
`DestinationCleanupResult` that explicitly attests destination-side
revalidation. Controller-side path deletion is never used for remote candidates.

### 3. Global authorization was inferred during apply

Global/user cleanup selection could be rebuilt from `scope` alone at apply time.
G2 binds `global_scope_authorized` into `CleanupPlan`; apply fails closed when a
non-dry-run global plan lacks that explicit authorization. Canonical safety
metadata still requires confirmation.

### 4. Partial/provenance truth was too optimistic

A plan containing both deleted and protected resources could report `applied`.
G2 makes every protected/partial branch contribute to a `partial` receipt and
writes tombstones only for resources whose postcondition is actually absent.

### 5. Ignore state was being treated as ownership

`descriptor_from_path(..., heuristic=True)` previously promoted an ignored path
to managed cleanup ownership. That violated LIFE-P069/LIFE-P105: Git ignore is a
heuristic, not producer ownership. G2 keeps heuristic discoveries unmanaged even
when ignored. Explicit provider/Devtool descriptors remain deletable; generic
`build`, `dist`, `.bak`, `.orig`, etc. do not become deletable from names/ignore
state alone.

## Primary-failure preservation

G2 adds `preserve_primary_lifecycle_failure()`. When a cleanup action fails after
an earlier lifecycle failure, the cleanup failure is retained as secondary
metadata/evidence but cannot replace the primary failure kind/exit code. If the
primary lifecycle succeeded, cleanup can still fail closed normally.

This helper is adopted by artifact cleanup on direct/bundle apply paths. Existing
validation finalization already classifies cleanup failure administratively.

## Adversarial proof

`tests/test_life_g2_ownership_retention_gate.py` proves:

- tracked source and ignored heuristic-only resources survive;
- global scope authorization and confirmation are both mandatory;
- a RUN attachment introduced after planning blocks apply;
- an active transaction introduced after planning blocks apply;
- an active validation session introduced after planning blocks apply;
- RER/seal references protect managed resources;
- content, policy and destination drift fail closed;
- mixed applied/protected cleanup remains partial and tombstones only deleted
  resources;
- remote cleanup executes only through a destination-local revalidating executor
  and never deletes the controller placeholder;
- a remote executor that cannot attest revalidation fails closed;
- secondary cleanup failure cannot replace a primary lifecycle failure;
- cleanup receipt/tombstone provenance remains inspectable after payload deletion.

## Validation

Construction host uses a temporary `desktop-linux` target override only to run
the Devtool workflow; production `.devtool.toml` is restored to
`native-termux` before packaging.

Authoritative construction workflow:

```text
./devtoolw life-g2
6 stages passed
101 passed, 0 failed, 0 skipped
0 warnings, 0 errors
```

Additional artifact-cleanup/primary-failure retained regression:

```text
10 passed, 0 failed
```

A broader retained cleanup/RUN/transaction sweep also passes **101/101**.

## Promise audit

G2 proves the ownership/retention milestone for LIFE-P063..P090, P092..P095,
P098, P099, P101 and P105. LIFE-P091 remains partially open only for
provider-specific global-cache lease/idle coordination where a provider exposes
such a lease. LIFE-P096/P097/P100 remain intentionally owned by LIFE-X04's
status/doctor/presentation convergence.

The gate does not absorb ANDROID-X03: Android instrumentation/device cleanup must
consume this canonical destination-qualified model without adding filename-based
Android deletion authority.
