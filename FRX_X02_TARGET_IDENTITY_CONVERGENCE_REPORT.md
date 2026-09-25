# FRX-X02 — Target Identity Execution & Observability Convergence

## Status

**PASS — audit loop closed with zero new material FRX-X02 gaps.**

This detour overlay is based on the authoritative post-FRX-X01 source state applied by the user at commit `d3e44c7`. Content hashes, rather than Git commit metadata, are the overlay precondition.

## Objective

FRX-X01 introduced backward-compatible `repository::target::workflow` references and canonical planning identity. FRX-X02 carries that resolved identity through execution and observability so target-qualified children remain unambiguous after planning.

FRX-X03 executable federation groups are intentionally deferred.

## Delivered contract

For workflow children, the canonical execution identity is now carried as:

```text
repository
target
child_workflow
federation_ref = repository::target::workflow
requested_ref
```

That identity now converges through:

- parent/child EXO lineage and node metadata;
- child re-entry environment variables;
- persisted child-run summaries;
- federation transport metadata;
- whole-run SSH and Termux/chroot delegation;
- returned remote-artifact producer provenance;
- runtime/machine observability;
- doctor projections;
- CLI status/run summaries;
- TUI child rendering.

Direct WRAP/TLJ children deliberately do **not** masquerade as federation workflows. They carry a distinct direct contract identity, for example:

```text
repo::target::wrapper:package
repo::target::job:lint
```

This keeps workflow identity and direct-contract identity semantically separate while retaining the same repository/target precision.

## Compatibility and failure semantics

- Existing persisted federation lineage that predates FRX-X02 remains readable when all new target/ref fields are absent.
- Once any new workflow identity field is present, the workflow identity tuple must be complete and internally consistent.
- A mismatched or partially populated canonical workflow identity fails closed rather than degrading to repository-only attribution.
- Legacy `repository::workflow` requests continue to resolve through the selected/default target, but execution/observability records the resolved canonical `repository::target::workflow`.

## Audit-loop findings fixed

The audit loop found and fixed four material convergence gaps:

1. **Explicit TLJ target attribution.** A target-qualified job could execute the explicit target while its bridge plan still labeled the repository default target. The plan now uses the actual resolved direct-job target.
2. **Persisted-lineage compatibility.** New strict transport identity initially rejected older persisted lineage. The contract now accepts all-old identity while failing closed on partial/mismatched new identity.
3. **Returned-artifact provenance.** OCI/SSH producer-identity allowlists initially stripped the new target/ref fields. They now retain the canonical child identity in returned artifact evidence.
4. **TUI target collapse.** The child summary previously rendered only `repository:status`, allowing two targets in one repository to collapse visually. It now prefers canonical workflow/direct-contract refs.

## Source changes

- `devtool/federation_execution.py` — authoritative target/workflow lineage, child environment, EXO node metadata, run summaries, and direct contract identity.
- `devtool/invocation_origin.py` — reconstruct target/ref/contract lineage from re-entry environment.
- `devtool/federation_transport.py` — transport schema, canonical-ref validation, compatibility rules.
- `devtool/federation_observability.py` — canonical target/ref projections for runtime, plans and runs.
- `devtool/federation_bridge.py` — target-correct WRAP/TLJ direct contracts and contract refs.
- `devtool/oci_resilience.py` — preserve target/ref/contract data in remote producer identity.
- `devtool/cli.py` — canonical child identity in human federation run/status output.
- `devtool/tui.py` — target/ref-aware child inspection and summaries.
- `docs/architecture/federated-repositories.md` — FRX-X02 architecture contract.

Historical FRX implementation reports are intentionally unchanged.

## Executable evidence

Focused and cross-surface acceptance proves:

- canonical ref persists through child environment, EXO snapshot, parent node, and run report;
- legacy references are observed under the resolved canonical ref;
- runtime and doctor machine projections expose target/ref;
- transport metadata rejects canonical-ref mismatches;
- whole-run SSH and chroot forwarding preserve target/workflow/ref/requested-ref;
- WRAP/TLJ direct bridge plans preserve the actual selected target and direct contract ref;
- returned producer identity retains the child target/workflow/ref;
- original FRX-X01 execution bridge continues to re-enter the explicit child target.

## Validation

Final frozen-tree audit restart:

```text
598 passed, 12 subtests passed
```

The suite included FRX, WRAP, EXO, SSH, Termux/chroot, PRoot/OCI, doctor, TUI and HUD consumers.

`python -m compileall -q devtool` also passed.

## Artifact/replay evidence

The final artifact uses exact per-path source hashes against the post-FRX-X01 baseline and contains 12 inventory entries: 11 source/test/doc changes plus this report.

Final fresh-base replay passed:

```text
Artifact validation: PASS
Exact source preconditions: 12/12
Post-apply hashes: 12/12
Replayed FRX acceptance: 142/142 passed
ZIP integrity: PASS
```

## Apply policy

FRX-X02 is a **non-gate detour overlay**. Apply it with `--no-validate`.

Its before-commit finalize hook runs:

```bash
pip install -e ~/Code/devtool
```

while restoring `devtool.egg-info` so editable-install refresh does not leak packaging metadata into the overlay commit.
