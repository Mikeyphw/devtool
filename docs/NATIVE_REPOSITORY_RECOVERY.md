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

## Credential boundary closure: explicit inbound dependencies and live rechecks

A recovery deletion now requires an explicit schema-1 `--credentials-policy`.
This declaration may contain an empty `credentials` list only when the operator
has reviewed external callers and there are no known dependencies. An empty
snapshot, unavailable API, or lack of target-local secrets is not an automatic
inbound dependency inventory. The generic policy supports declared PAT and
GitHub App dependencies, including cross-repository secret hosts. The original
backup and credential-risk authorization remain separate.

Preparation seals the bound policy and readiness hashes, exact declared
repository IDs and secret-host metadata, any known inbound profile, and the
current GitHub administrator identity. The explicit
`DEVTOOL_RECOVERY_ADMIN_TOKEN` and actual GitHub CLI lifecycle identity must
both prove current admin permission for the original repository, with the same
account. These are read-only permission observations: they do **not** prove
future access after recreation, all write scopes, deletion/create permissions,
or organization policy. The existing backup, pre-deletion remote drift checks,
exact repository ID and exact `--ack-delete` guard remain in force.

Immediately before a possible delete (including a resumed `delete-intent`
whose original repository still exists), the engine independently evaluates
current credential feasibility and exact external identities. It compares the
result with the original anchored receipt; new obligations, changed identities,
permission loss, changed policy or readiness, or changed secret metadata block.
It writes a separate `credential-predelete-recheck.json` without rewriting the
original preparation receipt or automatically extending a waiver. A prior
`delete-intent` for an already absent original is reconciled, not replayed.
GitHub's DELETE-by-name API is not a compare-and-swap transaction: a residual
race with independent external GitHub mutations remains, so do not perform
concurrent repository administration while rebuilding.

Prepared legacy sessions lacking these new anchors must stop before a new
DELETE. Create and prepare a fresh verified session rather than editing their
journal. Completed historical sessions can still be inspected. A legacy
completed MMRL session without a credential policy may bind the reviewed
policy explicitly; a previously anchored wrong policy is not silently edited
or replaced. Such an old session needs explicit migration review, not a manual
journal edit or another deletion.

### Known MMRL federation profile

The reviewed live dependency is `Mikeyphw/MMRL-Shared`'s
`.github/workflows/n05f-propagate.yml`, not an assumed secret in MMRL-LSPosed.
It consumes `MMRL_FEDERATION_TOKEN` and propagates to `Mikeyphw/MMRL` (`next`)
and `Mikeyphw/MMRL-LSPosed` (`main`). Use the deliberately non-secret example
[`examples/recovery-mmrl-credentials.json`](examples/recovery-mmrl-credentials.json)
as a starting point. Verify the chosen protected endpoint permissions and
provide its dedicated PAT environment variable from a secure local source.
The original PAT should have access to both consumers and Contents write (the
actual hosted PR writes also prove the intended Pull requests permission).
Fine-grained repository selection cannot be automatically expanded to a newly
created repository ID: the existing separate
`--accept-credential-manual-risk` is still necessary, and does not prove future
access. Known MMRL dependency omissions or a wrong host are non-waivable.
The generic GitHub App path remains supported where explicitly authorized;
substituting an App for this particular deployed PAT workflow requires a reviewed
change to its workflow/dependency contract, not pretending the token type changed.

After recreation, a supplied PAT plus an Actions secret name is insufficient.
The engine independently checks the actual workflow blob, current host and
consumer IDs, exact current shared `main` source, protected PAT permissions,
current-secret timestamp, and a successful fresh exact-attempt N05F hosted run
created after the replacement and current secret update. Its exact job and
required steps, attempt-specific artifact name, bounded ZIP and SHA-256, and
both consumer receipt IDs/source and live PR identities must match. Both
consumer writes must be exercised; no-op or already-proposed rows do not prove
write access. This may require a normally authorized fresh propagation after
updating the federation consumer identity pin. No workflow is dispatched,
no production PR is opened, and no source or token is modified by evidence
collection. A changed workflow contract or unavailable evidence remains
`FEDERATION_NOT_VERIFIED` until reviewed. This is scoped federation credential
acceptance, not full restoration, build, device or release qualification.

`devtool recovery credentials --session PATH --policy POLICY --record` observes
and records proof. Provisioning/installation modifications still require the
separate explicit `--apply-pat --ack-pat-provision OWNER/REPO` or
`--apply-app --ack-app-repair OWNER/REPO` command and exact identity checks.
The original final verification no longer performs automatic App grants without
that approval. An unresolved PAT provision intent is not overwritten by an
automatic retry. The explicit Actions activation path independently checks
credential acceptance before enablement. PR CI qualification never erases an
unresolved credential classification. Historical `recovery report` reads and
validates the actual `credential-acceptance.json` digest and journal bindings;
`live_verified=false` is deliberate. Missing, unanchored, or inconsistent
receipts are not accepted as verified. Separate live `credentials --record`
is needed to refresh historical evidence.

### Disposable live acceptance (not normal CI)

`tests/native_recovery_e2e_disposable.py` remains opt-in and requires an exact
clean Devtool HEAD, exact owner/repository acknowledgement, and an already
created recent private standalone personal repository named
`repo-rebuild-e2e-<unique-id>`. Its existing restrictions on refs, issues, PRs,
secrets, and rulesets still apply. It requires an independent filesystem backup;
no archive gaps, local-only, missing-secret or blanket manual recovery waivers
are supplied by the harness. Give it `--independent-copy-dir PATH`,
`--expected-devtool-head SHA` and `--ack-disposable-delete OWNER/REPO`.
The clean fixture can use an explicit empty credential declaration generated
by the harness. For PAT/App tests provide a separate schema-1
`--credentials-policy` targeting **only** this fixture; no production consumer
or secret host is accepted. For PAT selection recovery, explicitly accept
`--accept-credential-manual-risk` when appropriate and perform repair after
the fixture has been recreated. Never re-run the destructive stage to repair
credentials. Use `--verify-session PATH` with the same source and fixture
acknowledgement, and exact `--ack-pat-provision` or `--ack-app-repair` only for
the separately authorized fixture write.

`disposable-live-acceptance.json` binds the source, old/new IDs, exact refs,
independent backup, pre-delete credential receipt, and post-recreation admin
proof. Credential-free lifecycle success does not qualify PAT/App grants;
these are separate fields and require an actual declared and verified fixture
credential. A PAT secret write is not hosted workload qualification. Hosted
workflow execution is not performed by this harness. Live tests must be
executed explicitly on disposable repositories with independently authorized
credentials: neither the offline nor the 24-shard regression is evidence of
live deletion, recreation, credential provisioning or installation mutation.
