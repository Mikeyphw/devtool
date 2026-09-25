# FRX-X01 Target-Qualified Child Workflow Identity Report

## Scope

FRX-X01 is the first detour overlay between GIT-FRX-01 and the remaining Git +
federation roadmap. It fixes the federation child-workflow reference limitation
without yet implementing executable federation groups or the full FRX-X02
execution/observability convergence.

The additive reference grammar is now:

```text
repository::workflow
repository::target::workflow
```

The legacy two-component form is preserved exactly: the child selected/default
target is used. The new three-component form selects the named child target
explicitly.

## Delivered

- `FederatedWorkflowStep` now retains explicit target intent without breaking
  the positional shape used by pre-existing callers.
- The federation parser accepts and normalizes both reference forms and rejects
  malformed arity or empty components fail-closed.
- Configuration machine output exposes `target` and `target_qualified` for each
  federation workflow step.
- FRX planning accepts an explicit requested target, validates that it exists in
  the child-owned target table, and validates the workflow specifically on that
  target.
- Legacy ambiguous-target behavior remains fail-closed, with a diagnostic that
  points users to `repository::target::workflow` when explicit selection is
  required.
- Once resolved, both reference forms normalize to canonical
  `repository::resolved-target::workflow` identity.
- Every executable federated plan node exposes that normalized identity as
  `federation_ref`; plan metadata also retains the original requested reference
  and whether target selection was explicit.
- Same-repository/same-workflow invocations on distinct targets produce distinct
  child graph identities because the existing local workflow node key already
  carries the target.
- The existing FRX-06 execution bridge consumes the planner's resolved target,
  so a target-qualified reference actually re-enters the child with the
  explicit `--target` argument. Full canonical target identity propagation in
  execution lineage/provider/UI/doctor/summary surfaces is intentionally left
  to FRX-X02.
- Current architecture documentation records FRX-X01 as an additive extension
  instead of rewriting historical FRX-05 evidence.

## Compatibility

Backward compatibility is preserved:

- `repository::workflow` keeps selected/default-target semantics.
- Federation schema remains version 1 because this is an additive grammar
  extension, not a breaking configuration shape.
- Existing federation groups remain metadata/observability only. FRX-X01 does
  not add `federation run --group`; that is reserved for FRX-X03.
- No existing FRX report was rewritten. Historical FRX-05 evidence remains
  byte-identical to the post-GIT-FRX-01 baseline.
- No Git transaction behavior from GIT-FRX-02 or later was pulled forward.

## Acceptance evidence

The FRX-X01 contract explicitly covers:

- legacy two-component parsing;
- target-qualified three-component parsing;
- normalized serialization;
- malformed and empty reference components;
- explicit non-default target selection;
- legacy default-target compatibility;
- explicit selection resolving an otherwise ambiguous multi-target child;
- unknown explicit target rejection;
- workflow-not-present-on-target rejection;
- same workflow name on two child targets without graph-key collision;
- canonical `federation_ref` in machine plan output;
- actual execution re-entry with `--target <explicit-target>`.

## Audit loop

### Pass 1

The parser/planner implementation was added and the original FRX suite was
restarted. This pass confirmed that the new syntax does not regress the sealed
FRX-01..12 behavior and proved explicit-target execution selection.

Result after the first convergence pass:

```text
132 FRX tests passed
```

### Pass 2

Consumer inspection covered config serialization, observability, CLI planning,
completion/help, doctor-facing consumers, and the newly landed GIT-FRX-01 state
surface.

One unrelated existing failure was encountered:

```text
tests/test_rem03_cli_config_discovery.py::test_feature_flags_and_doctor_use_explicit_config_path
```

It was reproduced unchanged on the untouched post-GIT-FRX-01 baseline. The
failure is the already-existing repository config-ownership contract rejecting
a config located in a descendant `configs/` directory; it does not originate
from FRX-X01.

This audit pass also caught an evidence-hygiene issue in the work itself: an
early draft modified `FRX05_FEDERATED_PLAN_GRAPH_REPORT.md`. That change was
removed, restoring the historical report exactly. Current behavior is documented
only in architecture docs and this new FRX-X01 report.

### Final restart

After the evidence fix and CLI machine-output regression were added, the audit
was restarted from the reference parser through the complete FRX consumer set.
No new material FRX-X01 gaps were found.

Final focused FRX result:

```text
133 passed
```

Adjacent consumer regression result:

```text
101 passed, 1 inherited failure deselected, 32 subtests passed
```

Additional verification:

```text
python -m compileall -q devtool: PASS
historical FRX05 report baseline comparison: IDENTICAL
```

## Deferred intentionally

The following are **not** part of FRX-X01:

- canonical target identity on every execution-lineage/environment/provider
  field and human run-summary label (FRX-X02);
- executable `[federation.groups]` / `federation run --group` (FRX-X03);
- adoption of the proven real cross-repository validate-ready/build-ready/
  test-ready graphs and final detour re-seal (FRX-X04).

## Validation policy

FRX-X01 is not a campaign validation gate. The overlay declares validation
explicitly disabled/deferred and should be applied with `--no-validate`.

The before-commit finalize hook refreshes the installed Devtool source using:

```bash
pip install -e ~/Code/devtool
```

while preserving/restoring `devtool.egg-info` metadata.

## Exact-base artifact replay

The packaged overlay was validated against a fresh copy of the authoritative
post-GIT-FRX-01 tree:

```text
Artifact validation passed.
source preconditions: 7 / 7
post-apply payload hashes: 7 / 7
zip integrity: PASS
focused replay FRX suite: 133 passed
adjacent replay consumer slice: 88 passed, 32 subtests passed
```

The adjacent replay slice intentionally omits the separately reproduced
inherited explicit-config-ownership failure. No implementation or test source
changed between the final zero-gap audit pass and artifact replay; only this
report was finalized with replay evidence before the final package hash was
computed.
