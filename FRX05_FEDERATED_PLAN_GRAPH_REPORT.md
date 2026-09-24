# FRX-05 Federated Plan Graph Report

FRX-05 adds planning-only cross-repository workflow DAGs on top of the sealed
FRX-01..04 identity and boundary contracts.

Delivered:

- repository-owned `[federation.workflows.<name>]` schema;
- compact `depends_on = ["repo::workflow", ...]` prerequisite shorthand;
- structured `steps = [{ id, ref, depends_on }]` for explicit cross-repo edges;
- deterministic topological ordering and cycle rejection;
- child-owned workflow expansion through the FRX-03 boundary resolver;
- namespaced child graph nodes (`repository::local-node`);
- child repository identity metadata on planned nodes;
- synthetic non-executable federation aggregate node;
- `devtool federation plan <workflow>` human and JSON surfaces;
- fail-closed missing repository/workflow, ambiguous target, duplicate graph,
  ownership drift, unknown dependency, and cycle handling;
- no child execution path in FRX-05.

Compatibility:

- repositories without federation workflows retain the exact prior empty
  federation machine-output shape;
- existing local/workspace workflow planning is unchanged;
- FRX-05 is a non-gate overlay and is applied with `--no-validate`.

Audit:

- FRX-01..05 focused suite: 54 passed;
- expanded FRX/MP/WRAP/TLJ regression slice: 172 passed;
- child-command non-execution test: pass;
- deterministic diamond graph test: pass;
- cross-repository cycle test: pass.
