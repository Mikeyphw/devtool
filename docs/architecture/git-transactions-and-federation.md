# Git transactions and federation

## GIT-FRX-01: authoritative repository Git-state model

GIT-FRX-01 establishes the read-only state boundary that later Git transaction
and federation overlays consume. It does **not** create private refs, journals,
transaction worktrees, amend commits, or mutate campaign history.

The public surface is:

```text
devtool git state
devtool git state --json
```

The state model records, from one canonical Git working-tree root:

- the existing FRX `RepositoryIdentity` plus a Git-repository ID;
- HEAD SHA, symbolic branch, detached/unborn truth;
- configured upstream and remote-tracking refs that already contain HEAD;
- canonical index tree SHA plus an index-entry fingerprint;
- staged, unstaged, untracked, and unmerged state;
- merge, rebase, cherry-pick, revert, and bisect state;
- current Git dir/common dir and a worktree-specific ID;
- every worktree registered in the shared Git common dir;
- shallow and partial-clone/promisor state;
- submodule relationships;
- object format and the Git capabilities used by the state collector.

### Repository ID vs. FRX repository identity

FRX repository identity remains the authoritative enrollment/config-ownership
identity used by federation. GIT-FRX-01 adds `repository_id` for Git transaction
state. It is derived from the shared Git common-dir identity and object format,
so linked worktrees of one local repository share a repository ID while retaining
distinct `worktree.id` values.

This distinction is intentional: a transaction may run in a temporary linked
worktree without becoming a different enrolled federation repository.

### Canonical paths and nested repositories

All input paths are canonicalized before Git discovery. A symlink alias therefore
reports the same canonical root and repository ID as the direct path. If the
requested path is inside a nested Git repository, Git's own top-level resolution
selects that nested repository rather than inheriting the surrounding repository.

This is state discovery only. GIT-FRX-07 later promotes explicit FRX enrollment
ownership rules into the Git subsystem for cross-repository path ownership.

### Index truth

`git write-tree` supplies the canonical index tree SHA when the index is
materializable, but Git may update the index's cache-tree extension while doing
so. Devtool therefore copies the index into an isolated `GIT_INDEX_FILE`, writes
new tree objects into an isolated temporary object database, and reads the real
object store only through Git's alternate-object mechanism. The state probe does
not modify the repository index or add tree objects to the repository. An
unmerged index cannot form a tree; in that case `tree_sha` is empty, `tree_error`
preserves Git's reason, and `unmerged` paths remain explicit. The collector does
not move refs, HEAD, branches, the index, or repository objects.

### Published-state evidence

`protected_remote_refs` is deliberately conservative groundwork for the later
amend guard. It contains the configured upstream plus remote-tracking refs that
already contain the current HEAD. GIT-FRX-04 will define the policy that decides
whether those refs make a commit unsafe to rewrite.

### Boundaries

GIT-FRX-01 does not:

- create `refs/devtool/*`;
- create a transaction journal;
- create or remove worktrees;
- amend, fix up, or create commits;
- rebase overlays;
- decide cross-repository ownership;
- synchronize state to remote workers.

Those responsibilities begin in GIT-FRX-02 and later roadmap overlays.
