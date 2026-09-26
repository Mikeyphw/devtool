# GIT-FRX-15 — Devtool-powered Git/federated bisect

## Scope and roadmap boundary

GIT-FRX-15 is implemented as the standalone non-gate milestone after FRX-Y10/Y11 and before GIT-FRX-16 Gate 2. The implementation preserves the roadmap-v1 search model: one repository varies while every other participating repository is pinned. Coordinated/Cartesian multi-repository search is intentionally rejected and remains future strategy work.

GIT-FRX-16 remote Git-bundle/object transport and remote attestation are not included. GIT-FRX-17 audit-delta/rerere/doctor work is not included. The previously discussed nested-tool event-forwarding proposal is also not included in this milestone.

## Public surfaces

Single repository:

```text
devtool git bisect --good <sha> --bad <sha> --task <task>
```

Federated:

```text
devtool federation bisect \
  --repo app=<good>..<bad> \
  --pin lib=<sha> \
  --task <federated-task>
```

Both surfaces support `--json`. Zsh completion and embedded completion data are synchronized.

## Execution and safety model

- Devtool never enters Git's global `git bisect` state.
- Candidate revisions are checked out only in disposable detached Git worktrees.
- User HEAD/branch, index, dirty tracked files, and untracked files remain untouched.
- A repository with an active Git operation is rejected before candidate search.
- `--good` must resolve to an ancestor of `--bad` for the default deterministic strategy.
- Candidate result `125`/skip is rejected rather than guessed across.
- Every candidate combination/result is persisted under the Devtool state root in `state.json` plus append-only `history.jsonl`.
- Failed searches persist explicit failed state/history rather than silently disappearing.
- Output is represented by digest in candidate evidence rather than copying arbitrary child output into the bisect ledger.

## Federated identity and authority reuse

Federated bisect requires an existing persisted frozen federation source lock for the requested workflow/group. Exactly one participating Git repository is allowed to vary. Every other participating repository defaults to the frozen lock's commit; repeatable `--pin NAME=SHA` may explicitly override a non-varying repository.

Each candidate identity records:

- complete repository commit combination;
- frozen lock digest, local/equivalence/graph identity;
- candidate federation graph digest;
- candidate capability-graph generation and digest;
- candidate artifact-input contract identity and digest;
- pin provenance (`frozen-lock` or `explicit`).

Actual federated evaluation reuses the existing federation planner/executor. Candidate worktree roots are scoped into a temporary parent configuration so normal federation execution can create an ephemeral candidate lock without mutating the user's persisted frozen lock. Resolved artifact-input evidence from execution augments the result identity.

## Native roadmap declaration

Target `devtool` now declares:

- TLJ `git-frx15-bisect-history-fixture`;
- first-class test `roadmap-git-frx15`;
- workflow `roadmap-git-frx15` with six nodes: source syntax, GIT-FRX-14 regression, FRX-Y11 regression, bisect fixture, GIT-FRX-15 tests, EXO/workflow baseline;
- `roadmap-current` includes GIT-FRX-15.

No editable-install validation hook is added because GIT-FRX-15 is an interim non-gate overlay.

## Construction audit evidence

- GIT-FRX-15 focused tests: **10/10 passed**.
- Focused + native declaration + completion + FRX-Y11 compatibility: **35 tests passed + 32 subtests passed**.
- GIT-FRX-01..08 regression block: **122/122 passed**.
- GIT-FRX-09..15 regression block: **45/45 passed**.
- Total GIT-FRX-01..15 regression evidence: **167/167 passed**.
- FRX-Y01, Y02, Y04..Y11 authority regression block: **96/96 passed**.
- Python compileall: passed.
- `git diff --check`: passed.

Native execution proof:

- `plan roadmap-git-frx15 --json`: passed, **6 nodes / 6 commands / 6 waves**, two target-local jobs.
- `preflight --workflow roadmap-git-frx15 --json` with explicit construction-host override: **healthy**, Python runner ready.
- Composite invocation advanced through source syntax, GIT-FRX-14 **2/2**, FRX-Y11 **14/14**, read-only bisect fixture, and GIT-FRX-15 **10/10**. The construction container retained the outer invocation before the final EXO stage returned, matching the previously observed outer teardown behavior.
- The remaining declared EXO/workflow baseline was therefore qualified independently through Devtool: **18/18 passed, 0 warnings, 0 errors**.
- Declared workflow stage qualification totals **44/44 tests**, plus successful source-syntax and bisect fixture stages.

## Delivery policy

This is an interim non-gate overlay. Apply with `--no-validate`. The overlay uses exact source SHA-256 guards against the post-FRX-Y10/Y11 source content and is intended to be replay-qualified byte-for-byte before delivery.
