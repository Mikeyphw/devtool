# GIT-FRX-13 — Transactional Dirty-Tree Preservation

## Scope delivered

GIT-FRX-13 removes the old requirement to fall back from isolated transaction worktrees merely because the primary checkout is dirty. Before an isolated apply, Devtool captures a transaction-private snapshot of the user Git index plus exact worktree bytes/modes/symlinks for staged, unstaged, deleted, and untracked dirty paths.

The transaction executes in the isolated worktree. Before promoting its result commit, Devtool validates that the preserved dirty state can be transplanted safely. It fails closed when the transaction starts tracking a previously-untracked user path, when an unmerged index would be crossed over a new HEAD, or when the transaction commit changed a path the user already had dirty.

For non-overlapping changes, the primary checkout is advanced to the transaction result and then the user's staged/unstaged/untracked state is restored. Same-HEAD restore preserves exact index bytes; changed-HEAD restore reconstructs only the captured staged index entries over the new result. Transaction rollback restores both the original HEAD and the original dirty state.

No `git stash`, temporary user commit, user branch, or destructive cleanup is used.

## Public/internal surface

- transaction-private dirty snapshot manifest and exact worktree backup;
- `capture_dirty_tree`, `validate_dirty_tree_rebase`, `restore_dirty_tree` primitives;
- isolated worktree promotion integrated with transaction rollback/recovery;
- campaign dirty-state restore bridge used by GIT-FRX-12;
- Self-hosted target-local fixture: `git-frx13-dirty-fixture`;
- First-class test identity: `roadmap-git-frx13`;
- Workflow identity: `roadmap-git-frx13`.

## Audit evidence

- Merged focused milestone contracts: 15/15 passed after hardening.
- Existing isolated-worktree transaction contracts included in the focused 30-test pre-hardening checkpoint and the final GIT-FRX-01..13 family.
- GIT-FRX-01..13 regression family: 155/155 passed.
- Native merged workflow `roadmap-git-frx11-13`: 9/9 stages, 57/57 tests, 0 warnings, 0 errors.
- Final fixture restored staged, unstaged, and untracked paths and reported `index_restore_mode=exact`.

## Promise ledger

- dirty primary checkout no longer forces direct transaction mode: delivered;
- exact staged/unstaged/untracked snapshot: delivered;
- isolated transaction promotion under preserved user state: delivered;
- rollback restores original HEAD + dirty state: delivered;
- untracked→tracked collision rejection: delivered;
- tracked dirty/transaction overlap rejection: delivered;
- unmerged-index crossing rejection: delivered;
- no stash/user-visible commit: delivered.
