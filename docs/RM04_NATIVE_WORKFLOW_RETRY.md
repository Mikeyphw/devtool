# RM-04 — Native workflow-node retry and recovery

RM-04 extends the existing RM-02 scheduler and RM-03 canonical EXO event projection. It does not create another execution engine, authenticate remote operations, or grant signing/publishing authority.

## Operation

A normal native workflow run with a clean Git source tree saves a private, atomic `workflow-retry.json` recovery record alongside its EXO run. The record freezes the commit and tree, native DAG node keys and dependencies, command/configuration fingerprints, executable toolchain hashes, host profile, input Merkle manifests, original outcomes, and verified output SHA-256 digests. Runs lacking these records are not retroactively considered retryable.

Review without executing:

```sh
devtool -r . --target TARGET workflow retry --run-id ORIGINAL_EXO_RUN_ID --failed --plan
```

Explicit local replay:

```sh
devtool -r . --target TARGET workflow retry --run-id ORIGINAL_EXO_RUN_ID --failed --yes
```

Resume an explicitly interrupted attempt, after reviewing its saved `workflow-retry-attempt.json`:

```sh
devtool -r . --target TARGET workflow retry --run-id ORIGINAL_EXO_RUN_ID --failed --yes --resume
```

The source run, graph, per-node signatures, toolchain binaries and host profile must still match. RM-03 EXO journal entries must prove terminal results for every original node. A successful independent node can be reused only if all declared original inputs **and** all declared output bytes still match. A rerun invalidates any reused descendants, regardless of their original output hashes. A node can execute again only if its job metadata declares `retry_safe = true` (or `read_only = true`) and it is not classified as remote/device/signing/publication/other irreversible work.

Example target-local job:

```toml
[targets.api.jobs.verify-contracts]
runner = "command"
command = ["python3", "scripts/check-contracts.py"]
inputs = ["src/**", "scripts/check-contracts.py"]
outputs = ["build/contracts.json"]

[targets.api.jobs.verify-contracts.metadata]
retry_safe = true # assert idempotence: repeatable operation with controlled writes
```

A task marked `retry_safe` is **not** granted any privileges. Safe scopes, target admission, isolation policies, and existing Git/remote restrictions continue to apply. Configuration declarations must be reviewed before use. Glob input/output hashing is bounded and rejects symlinks, traversal, excessive file sizes/counts and ambiguous source roots.

## Crash and duplicate safety

Atomic per-node start/end checkpoints are stored in `workflow-retry-attempt.json`, with output-byte proofs after terminal success. If a process is interrupted, an ordinary retry refuses to start again. Explicit `--resume --yes` revalidates source, plan, host, and every previously completed node's outputs; only incomplete, declared retry-safe nodes are eligible to run. A successfully completed attempt refuses duplicate execution. Missing output proof is never treated as completion.

## Limitations (explicit)

- An RM-03 or older run without an RM-04 source-bound recovery record cannot be automatically recovered.
- Only local native jobs, source-clean, exactly identified single-target workflows, and explicitly safe commands are supported. Remote dispatch, signing, publishing and uncertain device operations remain blocked.
- Cross-host replay, migration of source identities, or commands whose inputs cannot be fully bounded require a new reviewed workflow, not automatic retry.
- SHA-256 verifies local file bytes; it does not establish remote authentication, release qualification or software signing trust.
- Retry planning is non-mutating relative to the source repository and execution commands. Devtool's ordinary CLI run catalog may still record that a planning command was invoked.
