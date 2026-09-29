# EXEC-G1 — Reuse Correctness Milestone

## Position

- Roadmap: `EXEC-G1`
- Gate window: **3/3** (`EXEC-X01`, `EXEC-X02`, `EXEC-G1`)
- EXEC scope: **3/7**
- Full implementation plan: **16/63**
- Next overlay: **ENV-X03 — 17/63**
- Remaining before leaving this gate window: **0**
- Next major scope after this gate window: **ENV** (`ENV-X03`)

## Merge-window decision

Inspected the next three canonical items: `EXEC-G1`, `ENV-X03`, `EXEC-X03`.

Decision: **do not merge**. `EXEC-G1` is the separate authoritative reuse-correctness gate; `ENV-X03` owns environment resolution and `EXEC-X03` owns durable one-graph validation/checkpoint/control convergence.

## Gate audit scope

The gate reconciles the direct EXEC-X01/X02 reuse promises against code and bounded milestone evidence. It proves:

- stable node definition identity across run IDs;
- semantic definition changes invalidate prior reusable proof;
- isolated input changes do not invalidate independent branches;
- relevant environment/toolchain/safe-secret identities invalidate;
- presentation/ambient metadata does not enter semantic identity;
- missing or mutated outputs cannot silently qualify as reusable;
- legacy/incomplete/corrupt metadata fails safe to `RUN`;
- `REUSE`, `MATERIALIZE`, and `RUN` remain distinct;
- verified materialization requires digest-addressed provenance;
- fresh execution terminal proof can be re-qualified unchanged;
- reuse qualification is derived from EXO-owned terminal history and does not fabricate process execution;
- portability identity distinguishes native Termux/chroot/provider-bound contexts while allowing explicitly portable desktop/native equivalence.

## Owned correctness defect found and fixed

EXEC-G1 found a real EXEC-X02 defect in `aggregate_fingerprints()`.

For a single-spec workflow node, terminalization re-hashed every semantic fingerprint component as a one-item list. The stored terminal fingerprint therefore differed from the exact pre-execution fingerprint even when nothing changed. A fresh successful node immediately shadow-qualified as `INVALIDATED` instead of `REUSE`.

Fix:

- one-spec nodes now preserve the exact invocation/input/dependency/environment component digests;
- true multi-spec nodes retain ordered aggregation;
- the canonical node definition remains the aggregate owner;
- dependency terminalization still replaces the dependency digest with EXO-derived upstream result identities where applicable.

The new fresh-execution gate reproduces the former failure and now proves `REUSE` without creating any synthetic process record.

## Artifact validation / Devtool dogfooding improvement

EXEC-X02's artifact used a Devtool lifecycle but duplicated one raw pytest command in `validation.tests`. EXEC-G1 closes that gap in two layers.

### 1. First-class artifact validation refs

Artifact v2 validation now accepts:

```text
validation.refs = [
  "test:<test-id>",
  "workflow:<workflow-action>",
  "invoke:<workflow-action>"
]
```

`invoke:` canonicalizes to `workflow:`. Workflow refs are lowered through the existing workflow planner/scheduler and EXO execution path. They are not shell scripts and do not introduce another scheduler or history store.

Artifact inspection exposes the normalized refs, and required-validation policy recognizes refs as a valid authoritative validation surface.

### 2. Bootstrap-safe self-hosting of this gate

The EXEC-G1 artifact itself must be applicable by the pre-G1 Devtool process, which does not yet understand `validation.refs`. Therefore this one transition uses the already-supported first-class test-ref protocol:

```text
artifact validation.tests ref
        -> roadmap-exec-g1-artifact TestSpec
        -> ./devtoolw exec-g1
        -> wrapper workflow mapping
        -> invoke roadmap-exec-g1
        -> canonical workflow DAG / EXO / RUN evidence
```

The artifact manifest contains **no duplicated pytest argv**. The pytest commands live only in first-class TestSpecs referenced by the workflow.

After EXEC-G1 is installed, later artifacts can use `validation.refs = ["workflow:..."]` directly.

## Canonical gate workflow

`roadmap-exec-g1` contains five stages:

1. `source-syntax`
2. bounded X01/X02 foundation regression slice
3. stable identity / branch / environment portability milestone
4. EXO terminal proof / outputs / materialization / fresh-shadow milestone
5. artifact workflow-ref self-hosting contracts

The middle identity and evidence nodes are independent after syntax; the artifact-ref node waits for all gate evidence.

## Construction qualification

The source target is intentionally `native-termux`. The construction environment for this artifact is desktop Linux, so an unmodified wrapper run correctly failed environment preflight (`native-termux` requested on `desktop-linux`).

A disposable construction mirror changed only `targets.devtool.execution_environment` to `desktop-linux` and ran the exact canonical command:

```text
./devtoolw exec-g1
```

Result:

```text
5 workflow stages passed
65 tests passed
0 failed
0 skipped
0 Devtool warnings
0 Devtool errors
```

The raw underlying pytest run still exposes the previously exported LIFE-owned `hooks/engine.py` file-handle `ResourceWarning`; EXEC-G1 does not claim ownership of that defect.

A second disposable replay started from an exact pre-G1 source checkout and applied a construction-only twin of the final artifact. The twin differed only in that same `native-termux` → `desktop-linux` target-environment line. This exercised the pre-G1 artifact parser, transaction worktree, first-class TestSpec ref, wrapper re-entry, invoke/workflow DAG, EXO-backed execution, finalize hook, commit, verification, and cleanup:

```text
8 artifact lifecycle stages passed
5 nested workflow stages passed
65 tests passed
0 failed
0 skipped
0 Devtool warnings
0 Devtool errors
disposable replay commit: 7b57861
```

The exact final native-Termux archive also passes Devtool static artifact validation against the uploaded pre-G1 source. The user's live Termux artifact replay remains the authority for native-Termux runtime evidence.

## Promise reconciliation

Directly verified/strengthened at this gate:

- `EXEC-P001..P005` authority boundaries remain intact;
- `EXEC-P006..P013` semantic identity/fail-safe history contracts;
- `EXEC-P014..P024` input/environment/dependency/output/materialization qualification contracts;
- `EXEC-P025` reused qualification never fabricates process execution;
- `EXEC-P026..P027` source/reason/diff evidence remains machine-readable;
- `EXEC-P032` qualification/index operations remain history-derived and non-mutating;
- `EXEC-P034..P036` portability/native-build ownership boundaries;
- retained audit promise `VAL-P081`: Devtool roadmap/gate work dogfoods canonical validation/execution surfaces.

Authoritative selective skipping remains intentionally disabled until its planned later EXEC owner; this gate validates qualification correctness, not production skipping.

## Exported/non-owned findings

Still exported unchanged:

- LIFE: hook engine file-handle `ResourceWarning`;
- TEST/PRES: multi-suite artifact count projection can report only the final suite;
- TEST/RMP: stale hard-coded roadmap test/workflow identity allowlists;
- later EXEC: authoritative selective skipping, same-run/cross-run continuation and one-graph validation convergence.

## Result

`EXEC-G1` closes the EXEC-X01/X02 gate window with a real Devtool-native workflow qualification path and fixes the single-spec terminal fingerprint mismatch discovered by the gate audit.
