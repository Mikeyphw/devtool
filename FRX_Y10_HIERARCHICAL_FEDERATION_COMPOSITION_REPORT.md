# FRX-Y10 — Hierarchical Federation Composition

Status: **implemented and qualified**.

## Delivered contract

FRX-Y10 adds first-class nested federation references without flattening grandchild repositories into the parent graph:

- `repository::@federation::<workflow>`
- `repository::@group::<group>`

The parent federation owns exactly one direct nested-federation node. The child federation re-enters through its own `devtool federation run` / `--group` surface, owns its own repository children, provider scheduling, cancellation and recovery below that boundary, and preserves master → nested federation → repository-child lineage.

The implementation also provides:

- cross-repository composition-cycle detection;
- opaque parent scheduling/resource evidence for nested federation nodes;
- no remote-provider override at the parent boundary (the child federation owns its Y09 placement decisions);
- hierarchy-depth propagation for sequential and parallel execution;
- parent resume/reuse treating a nested federation as one direct child;
- cancellation state remaining at that direct-child boundary;
- direct-child repository attestation only at the re-entry boundary.

## Native Devtool execution

- Target: `devtool`
- Workflow: `roadmap-frx-y10`
- Declared test: `roadmap-frx-y10`
- TLJ fixture: `frx-y10-hierarchy-fixture`
- Interim milestone: user apply uses `--no-validate`.

The standalone native workflow completed successfully with **6/6 stages, 39/39 tests, 0 warnings, 0 errors**.

## Focused evidence

The final FRX-Y10/Y11 focused contract set passed **22/22**. Y10 coverage includes reference parsing, no-grandchild flattening, nested group re-entry, composition-cycle rejection, local re-entry ownership, opaque resume/reuse, nested lineage, cancellation boundary truth, and parallel hierarchy-depth propagation.
