# Federation GitHub App credentials — additive FRX authentication

This change reuses Devtool's **merged M4 GitHub App installation provider** for
explicitly opted-in federation source **bootstrap and sync**. The GitHub App
already configured by the operator at
`~/.config/devtool/github-app/env.sh` is the default credential source.
No new GitHub App, key or installation is created.

## Per-repository opt-in

In the **federation owner's** `.devtool.toml`, select `auth = "app"` for each
enrolled repository whose `origin` is an exact `https://github.com/OWNER/REPO.git`
URL and whose GitHub App installation is authorized for `OWNER/REPO`:

```toml
[federation]
schema = 1

[federation.repositories.shared]
path = "../MMRL-Shared"
auth = "app"

[federation.repositories.android]
path = "../MMRL"
auth = "app"

[federation.repositories.local-ssh]
path = "../private-ssh-service"
auth = "git"
```

`auth = "git"` remains the default (SSH, existing PAT, Git credential helper).
No implicit switch from SSH to HTTPS occurs, and no Git remote is rewritten.
`auth = "app"` requires HTTPS `github.com`; SSH/unknown hosts fail closed.
The repository must be in the account authorized by the configured App. A
separate owner's repository requires that owner's approved App installation
and a separately configured provider; this implementation does not guess or
switch installations.

When invoking `devtool federation bootstrap --apply --yes` or
`devtool federation sync --apply --yes`, only their **Git fetch** operations
use App installation credentials for opted-in repositories. All existing
frozen-lock checks, clean-worktree, source identity, non-fast-forward refusal,
recovery journals, and explicit mutation approvals remain authoritative.
`--apply`/`--yes` are still required for changes. A read-only plan does not
issue a token or fetch.

## Credential contract

- Configuration resolution prefers a **complete** four-variable
  `DEVTOOL_GITHUB_APP_*` environment. Otherwise it reads the already installed
  private `~/.config/devtool/github-app/env.sh` without invoking Bash.
- It only accepts literal `export KEY=value` assignments in an owner-only
  `0600` regular file under an owner-only private directory. Symlinks,
  injected shell commands, missing fields and unsafe permissions are refused.
- The M4 provider verifies App/installation identity, repository membership,
  scoped `contents:read` permission, RSA private key ownership and token TTL.
  HTTPS Git fetch uses M4's one-operation, FD-backed AskPass transport; the
  token is absent from the command line, Git config, remote URL, shell source
  lock, journal and output.
- `fetch --prune origin` is the **only** new Git subcommand permitted under the
  M4 credentialed transport. No `-c`, arbitrary URLs, custom refspec or
  `--upload-pack` override is permitted. The remote locator must match the
  frozen lock exactly.
- When the App fails, an explicit `auth = "app"` **never falls back to a PAT**.
  Use `auth = "git"` deliberately for existing SSH/PAT workflows.
- This change does not grant remote mutation permission, modify GitHub App
  permissions, or perform any installation management; it reuses the already
  qualified installation configured by the operator.

## Scope boundaries

This first implementation only integrates App credentials into the
FRX-Y11 **source management network fetch** path. It does not alter other
federation child execution/SSH provider transports, GitHub remote federation
execution, arbitrary Git commands, local worktree integration, Git pushes,
GitHub API account discovery, or Devtool native delivery auth. A future
credential dispatch extension must be separately scoped and validated.

## Acceptance

- Tests: `tests/test_federation_app_auth.py`, `tests/test_frx02_federation_config.py`,
  `tests/test_frx_y11_bootstrap_sync.py`, `tests/test_github_app_m4.py`.
- Exact source pin: `Mikeyphw/devtool` merged M4 `main` at
  `e8e5dba9308e11eb210786bbeb68a7d1891f92fc`.
- Apply the changed files only through Devtool's existing native executable
  overlay transaction. Record committed session/PR/hosted CI separately; local
  tests alone are not a merge or live on-device qualification.
