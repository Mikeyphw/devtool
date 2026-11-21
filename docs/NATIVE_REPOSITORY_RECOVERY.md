# Native GitHub repository recovery in Devtool

Scope: native source integration of RepoRebuild 1.5.0 into the Devtool CLI,
package, and regression suite. A repository recovery is a remote destructive
operation and **cannot be rolled back by a local Devtool Git transaction**.

## Ownership

- `devtool.recovery.engine` owns backup, GitHub repository recreation,
  metadata/refs/PR restoration and the original durable remote lifecycle
  journal. Original source and session receipts remain authoritative.
- `devtool.recovery.operational` owns fork default-branch/Actions restoration.
- `devtool.recovery.qualification` audits workflow source across restored
  branches and separately qualifies historical and live PR evidence.
- `devtool.recovery.credentials` owns fine-grained PAT access verification,
  guided repair, and approved GitHub App installation management.
- `devtool.recovery.provider` uses Devtool's `github_actions.resolve_token`
  and a bounded, origin-constrained REST transport for JSON metadata reads/writes.
  Existing `gh` is still used for repository-management operations and binary
  asset operations not covered by Devtool's GitHub Actions API.
- `devtool.recovery.native_cli` directly dispatches to the integrated engine
  from the canonical Devtool parser; it never launches a second CLI process.
- `devtool/cli.py` dispatches the first-party `recovery` command before ordinary
  workspace initialization. `devtool/core_cli_schema.json` is authoritative
  for parser, command identity and option metadata.

## Core commands

```
devtool recovery doctor
devtool recovery plan OWNER/REPO
devtool recovery prepare OWNER/REPO --independent-copy-dir PATH
devtool recovery execute --session PATH --ack-delete OWNER/REPO
devtool recovery status --session PATH
devtool recovery report --session PATH
devtool recovery verify --session PATH
devtool recovery workflow-audit --session PATH
devtool recovery qualify-pr --session PATH --pr NUMBER
devtool recovery credentials --session PATH --policy PATH --record
```

`devtool recovery execute` corresponds to the old `repo-rebuild run` operation.
Explicit acknowledgements and restored GitHub identity/source checks are kept.
All source backups, Git bundle checks, independent backup requirements and
recovery sessions are unchanged except newly created sessions default to
`~/.local/share/devtool/recovery/`. Existing RepoRebuild sessions may be
inspected with an explicit `--session` path; they are not moved or rewritten.

## Mandatory operational gates

1. Pre-deletion backup completeness and independently verified copy.
2. Original remote repository ID and exact owner/repo confirmation.
3. Credential recovery feasibility and declared external dependencies.
4. Irreversible operation authorization plus durable mutation intent.
5. Post-recreation Git refs/metadata/fork/default-branch restoration.
6. Actual credential authorization (Actions secret name is not proof).
7. Exact PR head/base and required workflow/run-attempt CI evidence.
8. Separate historical CI, operational recovery and release qualification.

A green `plan` is OBSERVED_ONLY. A local build pass is not a live recreation
qualification. A successful PR run after merge is historical evidence only.

## Security

The provider does not transmit credentials via command-line arguments. GitHub
App installation mutation and PAT secret provisioning require exact owner/repo
acknowledgements. A GitHub App token restricted to repository read access cannot
silently become repository administration authority. The admin operations still
require an appropriately authorized user/CLI identity; unsupported App account
operations fail rather than expanding access. Secrets never belong in overlays
or journals. After a write with an unknown outcome, reconcile with independent
GitHub reads before retrying, and preserve the original journal.

## Native delivery

Changes must be built from a **clean named feature worktree**. The native
builder stages source and a schema-v1 reviewed plan outside the repository,
then invokes the existing trusted `devtool.launcher_contract_v3` builder. The
builder enforces exact branch/commit/tree, source preimage and payload hashes.
Validation is mandatory and includes all native recovery tests. A completed
local commit is distinct from a durable remote delivery session and from hosted
CI. Use the native launcher/session for push, draft PR and required CI only
under the corresponding explicit approvals.

No user repository, fork, GitHub App installation, credential or PR should be
mutated while testing the native integration. Destructive end-to-end testing
requires the existing opt-in private disposable repository harness, a separate
independent backup and another exact approval; that harness is never part of
regular `unittest` discovery.

## Remaining integration boundaries

Devtool's local repository-pack engine archives local files and a selected
Git commit; it is not by itself a complete GitHub remote-state backup. The
recovery engine preserves its more comprehensive snapshots while sharing the
Devtool CLI/credential resolution/terminal/CI-facing semantics. Additional
specialized `gh` operations remain for GitHub account administration, LFS,
secret provisioning and binary asset operations; those are explicitly audited
rather than hidden behind a claimed generalized App provider.

## V2 current-source and credential fixes

This handoff is pinned to `860671c4782ce58b3344871248456c516fb8ce08`.
The Termux installer creates a new clean named feature worktree at exactly this
commit and a detached, clean trusted runtime. It does not rebase, reset or
modify an existing divergent worktree. If upstream changes, abort and review
that changed source and any open PRs before building another immutable overlay.

The packaging stage asks GitHub CLI for read-only open-PR and changed-file
inventories. A PR touching the native CLI, canonical parser/schema, GitHub
credential helpers, native launcher, recovery package, delivery session or
recovery tests/docs must be resolved or reviewed on a new source pin first.

Native recovery **read** requests go through Devtool's repository-scoped token
resolution. Generic repository-administration REST **writes** require an
explicit `DEVTOOL_RECOVERY_ADMIN_TOKEN` environment source. Before each write,
the same token must independently return `permissions.admin=true` for the exact
`OWNER/REPO`. Neither an ordinary `GH_TOKEN` nor an App installation token is
implicitly promoted to remote administration. A scope-specific API denial
still blocks even after a successful admin probe. The credential itself is
never written to payloads, archives, journals or logs. Credentials should be
loaded only from a secure local source; do not paste them in shell commands or
send them in diagnostic output.

This separate credential preflight does not authorize repository deletion or
recreation. Those actions remain independently gated in the recovery engine.
