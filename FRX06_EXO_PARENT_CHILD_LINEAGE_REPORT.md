# FRX-06 — EXO Parent/Child Execution Lineage

Status: implemented

FRX-06 adds executable federation orchestration without merging child execution
state into the parent run.  `devtool federation run <workflow>` owns one
coordinator EXO run and creates a distinct child EXO run for each federation
workflow step.  Parent nodes retain child run ids and child snapshots retain
first-class additive `run_lineage` metadata.

Key invariants:

- child run id is always distinct from the parent run id;
- child repository identity remains authoritative;
- parent run/node, workflow/step, requested/effective repository and execution
  environment are persisted in child lineage;
- lineage corruption (self-parent or mismatched child run id) fails closed;
- downstream steps are blocked after failed dependencies without creating fake
  child runs;
- the first concrete child failure code is preserved by the coordinator;
- cancellation propagates as cancelled federation state;
- snapshot schema remains 7 for backward compatibility; `run_lineage` is an
  additive field;
- child invocations re-enter canonical `devtool invoke` rather than introducing
  a parallel runner implementation.

FRX-06 is not a campaign validation gate and is intended for `--no-validate`.
