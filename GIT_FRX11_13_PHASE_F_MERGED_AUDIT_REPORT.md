# GIT-FRX-11 + GIT-FRX-12 + GIT-FRX-13 — Phase F Merged Audit

## Merge-window decision

The configured three-overlay lookahead evaluated GIT-FRX-11 through GIT-FRX-13. All three are non-gate Phase-F Git provenance/campaign/recovery mechanics on the same `devtool` target and transaction/federation authority boundary. They therefore form one coherent overlay without reducing roadmap scope. Their individual roadmap job/test/workflow identities and reports remain first-class.

The next roadmap item, GIT-FRX-14, is not included in this package.

## Delivered slice

- GIT-FRX-11: append-only `refs/notes/devtool` execution provenance consuming existing federation/EXO/frozen-lock/artifact/scheduler identities.
- GIT-FRX-12: multi-repository campaign generations, private generation refs, frozen Y-series contract identity, lock generations, and per-repository `git range-diff`.
- GIT-FRX-13: exact dirty-tree/index capture and fail-closed restoration around isolated transaction worktrees, including rollback and campaign recovery.
- CLI/completion surfaces for Git notes and campaign operations.
- Native workflows: `roadmap-git-frx11`, `roadmap-git-frx12`, `roadmap-git-frx13`, plus merged `roadmap-git-frx11-13`.

## Final measured audit

- Milestone-specific contracts: 15/15 passed.
- GIT-FRX-01..13: 155/155 passed.
- Core FRX: 119/119 passed.
- X/Y federation: 113/113 passed.
- SSH-02/03 boundary: 38/38 passed.
- Completion/help: 20 tests + 32 subtests passed.
- `plan roadmap-git-frx11-13 --json`: `ok=true`.
- `preflight --workflow roadmap-git-frx11-13 --json`: `ok=true`.
- Native merged workflow: 9/9 stages, 57/57 tests, 0 warnings, 0 errors, 26.15s.

This is an interim federation milestone. It intentionally has no full validation gate and no editable-install hook; user application is expected with `--no-validate`.
