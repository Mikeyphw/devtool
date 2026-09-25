# FRX-Y08 — Explicit Artifact Producer → Consumer Edges

## Result

FRX-Y08 is implemented as an interim federation milestone. It adds typed, immutable artifact edges between federation workflow steps without creating a second artifact, SSH-transfer, or execution-history authority.

## Delivered contract

- Federation workflow steps can declare named artifact inputs using `producer-step:artifact:registry-id-or-name` references.
- Optional expected artifact `type` and `schema` are validated fail-closed.
- The producer must be a transitive predecessor of the consumer.
- Arbitrary producer filesystem paths are rejected at configuration parsing time.
- Executable federation groups remap producer step IDs and artifact references together, preserving edge identity after group expansion.
- Producer artifacts are resolved from the child artifact registry/operational snapshot by immutable registry identity and SHA-256.
- Producer success, registry validity, availability, digest, type, schema, and repository-root containment are verified before consumption.
- Remote artifacts consume SSH-02 return lifecycle evidence and require the canonical `produced → staged → transferred → verified → restored_locally` state before they become consumable.
- SSH-02 remains the physical transfer authority; FRX-Y08 stores only the consumed edge identity/evidence and does not copy SSH run/history/lease state.
- Consumers receive a verified copy under their own `.devtool/federation-inputs/<parent-run>/<step>/...` area plus a managed manifest. Public edge environment data contains registry ID/digest/type/schema and a consumer-relative path, never the producer filesystem path.
- Consumed artifact identity is incorporated into the child checkpoint/result identity used by FRX-Y02 resume/retry.
- Artifact digest drift invalidates the consumer and its dependents while an otherwise unchanged producer can remain reusable.
- Group plans and ordinary federation plans expose declared artifact-edge metadata for machine inspection.

## Authority boundaries

FRX-Y08 owns producer→consumer edge semantics only. Child EXO/artifact-registry state remains authoritative for production and registry identity. SSH-02 remains authoritative for remote return lifecycle. FRX-Y02 remains authoritative for federation checkpoint/reuse decisions. GIT-FRX08/09 remain authoritative for frozen source and Git lineage.

## Native execution contract

Target: `devtool`

Workflow: `roadmap-frx-y08`

First-class test: `roadmap-frx-y08`

Target-local fixture: `federation-artifact-edge-fixture`

The fixture creates producer and consumer Git repositories, registers a typed producer artifact, verifies consumer-local materialization without producer-path leakage, mutates only the produced artifact digest under Devtool runtime state, then proves that the producer remains reusable while the consumer is invalidated specifically for consumed artifact digest drift.

## Audit-loop evidence

- FRX-Y08 focused contracts: 6 passed.
- FRX-Y08 + native roadmap declaration contracts: 10 passed.
- Core federation compatibility batch: 119 passed.
- Federation X/GIT-FRX compatibility batch: 79 passed.
- Federation Y01/Y02/Y04-Y08 compatibility batch: 70 passed.
- SSH-02/SSH-03 boundary contracts: 38 passed.
- Native `roadmap-frx-y08`: 6/6 stages, 52 tests passed, 0 failed, 0 skipped, 0 warnings, 0 errors.
- Source syntax/compileall: passed.

## Apply policy

FRX-Y08 is not a validation gate. Its overlay has fail-closed source SHA-256 preconditions and must normally be applied with `--no-validate`. It does not reinstall editable Devtool.

## Next boundary

The next roadmap milestone is GIT-FRX-10. FRX-Y08 intentionally does not absorb that Git milestone.
