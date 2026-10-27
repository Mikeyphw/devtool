# RM-03 — Canonical EXO node lineage, typed remote-evidence jobs

**Source dependency:** RM-00 provider/evidence contracts → RM-01 scopes → RM-02 dependency-ready scheduling.
**Owner:** Devtool core. **Status:** implemented and regression-covered (see promise audit); Termux acceptance happens when installed.

## Merge window
RM-03 and RM-04 remain separate. RM-03 is read-only correlation and event identity; RM-04 will add potentially mutating retry, fingerprint invalidation, and irreversible-operation guards. Combining them would allow a lineage defect to cause a duplicate dispatch or unintended retry and widen the rollback unit.

## Interfaces

`devtool -r . evidence lineage --run-id <EXO_RUN_ID> --json` reads an existing canonical EXO snapshot and its journal (or historical snapshot). It neither dispatches remote work nor rewrites event history.

`--remote-receipt PATH` (repeatable) attaches a **local observation receipt** only when `exo_run_id`, `exo_node_id`, source commit, repository, remote run and attempt agree. Missing EXO source binding rejects attachment; absent journal terminals remain `SNAPSHOT_ONLY` or `UNRESOLVED`, never upgraded to authenticated history.

For a configured target-local job:

```toml
[targets.devtool.jobs.check-remote]
runner = "remote-run-correlated" # or remote-build-verified / artifact-verified
action = "verify"
inputs = [".devtool/evidence/my-remote-observation.json"]

[targets.devtool.workflows]
check-remote = ["job:check-remote"]
```

The receipt must use schema `devtool.remote-observation` version 1. Required fields are `repository`, `source_commit` (40-hex), `exo_run_id`, `exo_node_id`, `request_id`, positive `remote_run_id` and `remote_attempt`, and a matching `remote` object. Builds require a bound `build` object and artifacts additionally require a bound `artifact` object with a real in-repository file and SHA-256. Absolute paths, `..`, symlinks, oversized manifests, changed file bytes, source/run/attempt mismatches, and duplicate bindings are refused. All three typed jobs execute **local checks**. They never call GitHub, mutate its state, or issue grants.

## Canonical EXO source and attempt metadata

Workflow lowering binds `workflow_source_commit`, `workflow_config_fingerprint`, `workflow_attempt=1`, and `workflow_canonical_identity=SHA256(source,target,workflow,node,attempt)` to command metadata. Existing EXO `node.discovered`, `node.started` and `node.finished` are preserved and extended; multi-part nodes publish one terminal per part plus one logical parent terminal, not an additional terminal per passing part. A skipped suffix after failure neither restarts nor re-terminates the failed parent. Existing event subscribers, graph, reducer, HUD, EXO store, and historical readers remain authoritative.

The read-only projection validates journal run IDs, monotonic sequences, duplicate terminal events, and snapshot/journal terminal contradictions. A missing/truncated/non-current journal is shown as incomplete, never treated as a reconstructed success. Run-level pipeline/federation references are projected as identity only, without verifying cross-repository provenance.

**Safety:** Local consistency does not prove GitHub authentication, trusted artifact producer, device acceptance, or release qualification. Those flags remain `false` even if the user's JSON declares success. Provider-backed authenticated remote attestation and complete artifact producer→verifier→consumer provenance are separate RM-05/RM-06 obligations, not silently claimed by RM-03.
