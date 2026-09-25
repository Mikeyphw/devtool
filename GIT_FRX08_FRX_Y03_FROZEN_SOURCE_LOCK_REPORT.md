# GIT-FRX-08 + FRX-Y03 — Federated Git Plan Graph + Frozen Multi-Repo Source Lock

## Outcome

Delivered one shared source-freeze authority for federated planning and execution. GIT-FRX-08 and FRX-Y03 are intentionally co-delivered: the Git plan graph and the frozen source lock cannot be independent models without risking contradictory source truth.

## Public surface

- `devtool federation plan <workflow> --freeze`
- `devtool federation plan --group <group> --freeze`
- `devtool federation lock`
- `devtool federation lock --update <repo>`
- `devtool federation run <workflow> --drift-policy fail|replan|allow-if-equivalent`

The normal plan also carries an embedded source snapshot even when `--freeze` is not used, so a repository move between planning and execution is still detected.

## Lock model

The lock uses three identities:

1. `lock_digest` — portable strict source identity. Includes source/config/graph semantics while excluding local filesystem paths.
2. `local_digest` — adds local repository/worktree identity for same-host drift detection.
3. `equivalence_digest` — ignores commit/ref placement but still requires equivalent tree/index/dirty/config/graph content. Used only by explicit `allow-if-equivalent` policy.

Per repository, the lock records repository/source kind, Git repository identity when available, local root, repository/config fingerprint, start commit or unborn state, branch/ref/upstream, HEAD tree, index tree/fingerprint, dirty identity, worktree state, commit policy, expected result ref and node dependencies.

Explicit non-Git enrolled repositories are represented by deterministic filesystem-tree content identity without fabricating Git state.

## Drift policies

- `fail` — exact planned source state is required.
- `replan` — source drift explicitly rebuilds and replaces the frozen plan.
- `allow-if-equivalent` — permits different commit/ref placement only when portable source/config/graph content is equivalent.

`lock --update <repo>` refreshes one repository only when graph identity is unchanged. Graph changes require a full re-freeze.

## Safety boundaries

- No clone/fetch/pull/checkout/reset is performed. FRX-Y11 remains the owner of bootstrap/sync.
- Every execution validates the source snapshot embedded in the plan, including unpersisted plans.
- Frozen identity is path-independent where portability is required.
- Local worktree/repository identity remains available for strict same-host checks.
- Existing FRX ownership and child-owned configuration remain authoritative.

## Native roadmap contract

- Target: `devtool`
- Workflow: `roadmap-git-frx08-freeze`
- Declared test: `roadmap-git-frx08-freeze`
- TLJs: source-lock fixture, drift mutation probe, lock inspection fixture
- Native plan: 6 nodes / 6 waves / 3 TLJs
- Desktop analysis preflight stops only at the expected `native-termux` target mismatch.

## Validation evidence

Post-final-fix results:

- focused freeze contract: 17 passed
- freeze + native roadmap contract: 21 passed
- GIT-FRX-01..04: 68 passed
- GIT-FRX-05..08/Y03: 54 passed
- total GIT-FRX-01..08/Y03: 122 passed
- Gate-1 regression aggregate: 103 passed
- compileall: PASS

Earlier bounded campaign sweeps also completed cleanly before the final race hardening:

- core FRX: 119 passed
- X/Y + native roadmap: 102 passed
- completion/help surfaces: 34 tests + 32 subtests

The final race hardening only changed plan-to-execution frozen-source verification and its dedicated regression test; the affected focused/Git/Gate-1 suites were rerun afterward.

## Apply policy

This is not a validation gate. Apply with `--no-validate`. There is no finalize/reinstall hook.

## Packaged replay evidence

The final overlay was replayed through the pre-GIT-FRX-08/Y03 Devtool at baseline `e467a9d` with validation disabled, as required for this non-gate milestone. The transactional apply completed with 0 warnings and 0 errors, committed/integrated cleanly, all 11 payload hashes matched the packaged inventory, and the replayed tree passed 21/21 focused frozen-source + native-roadmap tests.
