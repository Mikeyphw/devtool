# FRX-Y06 + FRX-Y07 — Interface Conformance and Public Contract DAG Nodes

## Scope

This non-gate milestone combines two adjacent federation-intelligence items with one ownership boundary:

- **FRX-Y06** adds parent-declared federation interfaces and read-only child conformance inspection.
- **FRX-Y07** promotes already-public child workflows, target-local jobs (TLJ), and wrapper commands (WRAP) to typed federation DAG contracts without creating a second execution path.

GIT-FRX-07 is intentionally excluded because Git path/repository ownership is a separate subsystem and recovery boundary.

## FRX-Y06 — interface / conformance contracts

The federation schema accepts versioned interfaces under `[federation.interfaces]` with:

- `required_workflows`
- `required_wrapper_commands`
- `required_artifacts`

Executable or metadata federation groups may bind one interface with `interface = ".../vN"`.

Conformance inspection loads each enrolled child using the normal child-owned configuration boundary (`allow_parent_config=false`) and never writes child configuration. It reports:

- workflow names and owning child targets;
- wrapper command names;
- normalized artifact kinds derived from child target/artifact outputs;
- exact missing workflow/wrapper/artifact requirements;
- group/interface membership and aggregate health.

Public surfaces:

```text
devtool federation interfaces
devtool federation conformance --interface <name>
devtool federation conformance --group <group>
```

The existing federation preflight/gate report now includes interface conformance checks. Y05 impact intelligence also projects affected interfaces, but only through explicit group→interface bindings; no source-text or directory-proximity inference is introduced. Nested executable groups reuse the existing group materializer so repository membership has one authority.

## FRX-Y07 — typed WRAP/TLJ/workflow federation DAG contracts

Federation workflow/group refs now accept:

```text
repo::target::workflow:<name>
repo::target::job:<name>
repo::target::wrapper:<name>
```

Legacy workflow refs remain accepted:

```text
repo::workflow
repo::target::workflow-name
```

Typed identity is additive. A target-qualified workflow keeps its established `federation_ref` compatibility identity while machine metadata/lineage also expose:

```text
contract_type
contract_name
child_contract        # workflow:<name> / job:<name> / wrapper:<name>
contract_ref           # repo::target::<typed-contract>
```

Direct `job:` and `wrapper:` DAG nodes are planned through the existing `federation_bridge` services. Their execution is also routed through the existing bridge primitives:

- target-local jobs use the same resolved `OperationSpec` / `run_specs` service as `federation exec ... job`;
- wrappers use the same wrapper resolver/execution service as `federation exec ... wrapper`.

No synthetic child workflow is required merely to expose an already-public job or wrapper command.

The Y01 scheduler remains authoritative. Direct contract nodes publish conservative child scheduling scopes and global reservation evidence; dependency-ready behavior, failure→blocked transitions, cancellation, and parallel scheduling remain owned by the existing federation scheduler.

## EXO, machine, transport, and provenance truth

Parent federation node evidence and child lineage carry the typed contract fields. Existing invocation-origin, observability, and transport code already consume `DEVTOOL_FEDERATION_CHILD_CONTRACT` and `DEVTOOL_FEDERATION_CONTRACT_REF`; Y07 now supplies those fields for DAG execution as well as direct bridge execution.

Legacy custom workflow executors that persist the pre-Y07 lineage shape remain accepted. Default Devtool execution emits the new typed workflow fields, but their presence is not retroactively required from older custom workflow child snapshots. Direct job/wrapper contracts are required to preserve their typed identity because that identity is intrinsic to the Y07 node.

## Native Devtool execution contract

Two independent roadmap identities remain visible despite co-delivery:

- `roadmap-frx-y06` / `test:roadmap-frx-y06`
- `roadmap-frx-y07` / `test:roadmap-frx-y07`

Reusable read-only TLJs:

- `interface-conformance-fixture`
- `public-contract-dag-fixture`

Both native workflows compose source syntax, one fixture, the previous milestone test, their own declared pytest contract, and workflow/EXO baseline evidence. `roadmap-current` includes Y06 then Y07 after Y05.

This is **not a validation gate**. The artifact must be applied with `--no-validate`, contains no finalize hook, and does not reinstall Devtool.

## Audit evidence

Focused and compatibility evidence produced during construction:

- Y06/Y07 + native self-hosting focused set: 18 passed.
- Existing X01/X02/Y01 plus typed Y07 compatibility: 49 passed during the typed-workflow compatibility correction.
- Full federation/config/planner/execution/bridge/completion/roadmap regression sweep after final changes: **233 passed + 32 subtests**.
- Prior Gate-1 regression aggregate after final changes: **103 passed**.
- Declared Y06 test: **6 passed**, JUnit emitted.
- Declared Y07 test: **8 passed**, JUnit emitted.
- `roadmap-frx-y06` native plan: **5 nodes / 5 conservative waves**.
- `roadmap-frx-y07` native plan: **5 nodes / 5 conservative waves**.
- Native preflight/invoke on the analysis host stops only at the truthful execution-environment boundary (`native-termux` target versus `desktop-linux`). No target/environment contract was weakened.

The packaged-artifact replay is recorded separately in the audit evidence bundle after final ZIP construction.

## Packaged replay

The packaged overlay was applied using the pre-FRX-Y06/Y07 Devtool from baseline `835fdb1` with `--no-validate` and `--keep-artifact`: 0 warnings, 0 errors, clean commit/integration. All 18 packaged payload hashes matched the replayed repository and the replayed tree passed 19/19 Y06/Y07 + native-roadmap tests.
