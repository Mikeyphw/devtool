# M7 — Credential Doctor, authorized recovery and repository identity gates

Status: **native-delivery candidate**; *not* automatically a merged, hosted, device or release qualification. Built on M4 GitHub App provider and native GitHub Actions GET client. Does not add a replacement GitHub API client, key store, delivery journal or apply engine.

## Canonical commands

```text
devtool credentials doctor --repo Mikeyphw/devtool --auth app --probe-read
devtool credentials doctor --repo Upstream/Repo --head-repo ForkOwner/Repo --auth app --probe-read
devtool credentials doctor --repo Mikeyphw/devtool --auth gh
devtool credentials doctor --repo Mikeyphw/devtool --auth ssh --probe-read
devtool credentials doctor --repo Mikeyphw/devtool --auth app --probe-read --save-baseline "$HOME/.local/state/devtool/credential-doctor/devtool.json"
devtool credentials drift --repo Mikeyphw/devtool --auth app --probe-read --baseline "$HOME/.local/state/devtool/credential-doctor/devtool.json"
devtool credentials repair --repo Mikeyphw/devtool --auth app
devtool credentials predelete --repo Mikeyphw/devtool --auth app --probe-read --backup-sha256 EXACT_BACKUP_SHA256
devtool credentials postrecreate --repo Mikeyphw/devtool --auth app --probe-read --previous-repository-id 1276037219
```

The baseline's **parent directory must already exist**, be owned by the current user, and be mode `0700`. Saving is explicit, private, non-overwriting and non-symlink. A local baseline is advisory; it is not provider-signed proof and must never approve a destructive operation.

## Identity, token expiry and effective permissions

- `--auth app` is opt-in, consistent with M4. The private `~/.config/devtool/github-app/env.sh` is parsed by the existing federation/M4 safe loader without executing a shell. Explicit complete `DEVTOOL_GITHUB_APP_*` environment variables take precedence. PAT, `gh`, SSH and `auto` are separate modes; credentials do not silently switch identities.
- The default App probe is **GET-only** using a temporary signed JWT to read the configured installation and its actual repository installation. It verifies the App ID, installation ID, account owner and numeric account ID, suspension state, repository selection and published operation-specific permissions. It does not mint or print an installation token.
- `--probe-read` explicitly asks the **existing M4** provider to issue **one temporary Contents:read, single-repository installation token**, verify actual `GET /repos/OWNER/REPO` and the immutable repository ID, and check its expiry. The token and private key never appear in stdout/JSON/baseline/clipboard/argv/URLs. This token creation does *not* mutate the repository. A token issued for the read probe is *not* the installed provider's existing token and does not establish the latter's expiry.
- Fork/head identity requires a separate verified `DEVTOOL_GITHUB_APP_HEAD_*` environment configuration. A base installation cannot silently prove access to a different fork owner. The optional `--head-repo` runs an independent effective read probe and keeps the two credential scopes separate.
- The six supported App operations map to existing M4 permissions: `repository_read`, `pr_read`, `pr_create`, `branch_write`, `checks_read`, `actions_dispatch`. A granted installation capability is metadata evidence, not proof an attempted write would succeed. **No write operation is executed**. Workflow-file update permission is independently reported as not granted/unverified unless the App grants `workflows:write`.
- PAT/`gh` modes verify a repository-specific read using the existing Actions read-only client. Where GitHub supplies a token-expiry header, it is checked; otherwise expiry is explicitly unknown. Repository metadata does not prove fine-grained PAT write scope, SSH push access, organizational SSO, or branch protection bypass. SSH uses opt-in batch-mode `git ls-remote` with strict host-key checking, never `push`.
- Every observation is source-independent of delivery session *mutations*; token validation cannot approve a push, PR, workflow dispatch, permissions update, repository deletion or merge.

## Read-only repair, drift and recreation

- `repair` returns guided GitHub authorization/permission actions. No token rotation or App installation modification is performed. States are distinct: `VALID`, `EXPIRED`, `WRONG_INSTALLATION`, `MISSING_REPOSITORY`, `PERMISSION_DENIED`, `REQUIRES_APPROVAL`, `PROVIDER_UNAVAILABLE`, `UNVERIFIED`.
- `drift` compares an explicitly saved private baseline with new live observations, including changed App/installation/account/repository IDs, selection, permissions reductions and a **public key** fingerprint. It does not inspect secret contents in reports or treat a missing observation as a passed check.
- `predelete` always emits `BLOCKED`: access to a current repository and possession of a backup digest cannot prove authority to recreate a *different* repository ID, reauthorize tokens or restore every dependent repository. This safety gate intentionally supplies no deletion command. Any upstream destructive workflow must integrate its own independently authenticated future-owner and backup feasibility proofs before implementing an authorized deletion.
- `postrecreate` compares the newly verified immutable repo ID with the prior one and independently checks current read access and the capability matrix; it remains `BLOCKED` until dependent repositories and federation/Actions acceptance have their *own* verified evidence. A CLI flag cannot self-assert these proofs. Native commit/session, required CI and release qualification are not inferred.

## Acceptance and known boundaries

Focused tests cover metadata-only and effective read-token paths, identity mismatches, permission loss, expiry, missing workflow rights, baseline storage/redirection, provider error redaction, repository recreation, PAT expiry headers and read-vs-write separation. Native validation must rerun these tests alongside the original M4 provider tests at the exact source/target SHA. Hosted PR/CI24, real Termux/SSH/PAT runtime, App installation consent, migration authorization, and post-recreation dependent CI are separate gates. This delivery intentionally does not create a background scheduler or webhook receiver (M8), nor coordinate multi-repository transactions (M9).
