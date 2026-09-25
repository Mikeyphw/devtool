# GIT-FRX-07 — Federated Path-to-Repository Ownership Resolver

## Outcome

GIT-FRX-07 promotes the already-sealed federation enrollment/boundary rules into the Git subsystem without changing earlier FRX discovery semantics.

The new `devtool.git_ownership` authority:

- resolves the longest explicitly enrolled canonical repository root through the existing recursive FRX resolver;
- canonicalizes symlinks before ownership decisions;
- uses filesystem Git discovery only as a **negative boundary probe**, never as implicit federation enrollment;
- rejects ownership inheritance into an unenrolled nested Git checkout;
- rejects outside/sibling paths and symlink escapes;
- preserves explicitly enrolled nested repositories as independent owners;
- attaches stable Git `repository_id` plus worktree identity when the owner is a Git root;
- preserves logical ownership for explicitly enrolled non-Git repositories without inventing Git identity;
- remains deterministic for linked Git worktrees because `repository_id` is derived from shared Git-common identity while `worktree_id` remains worktree-specific.

## Federated plan evidence

`FederatedPlanGraph` now exports `repository_ownership` keyed by participating repository. Every non-synthetic child node also carries the same `git_ownership` evidence in metadata. This gives GIT-FRX-08/Y03 a single ownership substrate to freeze instead of rediscovering repository boundaries.

Existing node identity, dependency edges, scheduler behavior, capability graph, typed workflow/job/wrapper contracts, and child EXO ownership are unchanged.

## Native roadmap execution

Added:

- TLJ `repository-ownership-fixture`;
- first-class test `roadmap-git-frx07`;
- workflow `roadmap-git-frx07`;
- `roadmap-current` dependency on GIT-FRX-07.

Native plan evidence:

- workflow: `roadmap-git-frx07`;
- 5 nodes / 5 waves;
- refs: `job:source-syntax`, `job:repository-ownership-fixture`, `test:roadmap-frx-y07`, `test:roadmap-git-frx07`, `test:roadmap-workflow-exo-baseline`;
- preflight on the analysis host stops only at the expected `native-termux` vs `desktop-linux` environment mismatch.

## Focused promise coverage

The GIT-FRX-07 contract covers:

1. recursive longest-root ownership;
2. symlink-safe canonicalization;
3. symlink escape rejection;
4. unenrolled nested Git exclusion without auto-enrollment;
5. outside/sibling rejection;
6. linked-worktree stable repository identity and distinct worktree identity;
7. versioned machine-readable ownership payload;
8. plan-level repository ownership evidence;
9. node-level ownership evidence;
10. non-Git explicit ownership without fake Git state.

## Audit loop

- GIT-FRX-07 + native roadmap focused: **14 passed**.
- GIT-FRX-01..07 regression set: **105 passed**.
- FRX-01..12 + X01..X04 + Y01 + Y04..Y07 regression set: **217 passed**.
- Gate-1 regression aggregate: **103 passed**.
- Declared `roadmap-git-frx07`: **10 passed**, JUnit emitted at `.devtool/reports/roadmap/roadmap-git-frx07.xml`.
- `python -m compileall -q devtool tests`: **PASS**.

## Overlay lifecycle

This is an ordinary post-Gate-1 milestone:

- apply with `--no-validate`;
- no finalize hook;
- no editable Devtool reinstall;
- touched-files base contract against the exact post-FRX-Y06/Y07 baseline.

## SSH roadmap convergence

The separately updated SSH roadmap now consumes this resolver as the only federated/nested path ownership authority for SSH workspace seeding, reconciliation, return routing, GC/path safety and artifact restoration. SSH may use Git discovery to reject an unenrolled nested checkout, but never to auto-enroll one.
