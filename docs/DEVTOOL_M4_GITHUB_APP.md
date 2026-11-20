# M4 — Native GitHub App installation authentication

Status: **implementation candidate**. This document does not attest native apply,
GitHub App installation authorization, a hosted PR, hosted CI, a device test or
24-shard acceptance. All of those require fresh exact-source evidence.

## Existing authority reused

The existing native delivery session remains the sole source-bound mutation
journal. `delivery session push`, `pr` and `ci` reuse native `delivery_github`
and `GitHubActionsClient`, rather than opening a new PR/CI engine. An App credential
proves an identity with limited permissions, **not operator authorization**.
Push and draft PR creation still require separate `--yes`/native approval;
CI observation is read-only unless independently authorized elsewhere. No
unapproved Actions dispatch, merge, release or repository recreation occurs.

## Configure an App, explicitly

1. Create a GitHub App under GitHub Settings > Developer settings > GitHub Apps,
   then install it on the **specific** head repository (and separately on the
   upstream base if using a fork). Keep the App's private RSA key under a
   user-owned, non-symlink regular file (`0600`, containing directory not writable
   by group/others). Never put it in the repository, shell command, log, ZIP,
   manifest, remote URL, clipboard, or GitHub CI artifact.
2. Grant only required GitHub permissions for the selected operation. For a
   Git push, Contents write; for draft PR creation, Pull requests write plus Contents read; for CI
   observation, Actions read, Checks read and Contents read. GitHub itself may require other
   permissions for workflow-file updates. A 403/404 is a denial or uncertainty,
   not proof the repository is absent. Installation selection and permission
   changes require GitHub's own approval flow.
3. Configure **variable references**, not credential values, in the Termux shell
   environment before invoking Devtool; a pre-existing secured credentials
   store may set these names. The key **path** is supplied rather than its
   contents. The variables are intentionally not automatically selected by
   `--auth auto`:

   - `DEVTOOL_GITHUB_APP_ID`
   - `DEVTOOL_GITHUB_APP_INSTALLATION_ID`
   - `DEVTOOL_GITHUB_APP_ACCOUNT` (owner of the selected repo)
   - `DEVTOOL_GITHUB_APP_PRIVATE_KEY_PATH` (absolute key path)

   For an upstream fork PR where the head repository belongs to a different
   owner, additionally configure a distinct installation for HEAD reads:

   - `DEVTOOL_GITHUB_APP_HEAD_ID`
   - `DEVTOOL_GITHUB_APP_HEAD_INSTALLATION_ID`
   - `DEVTOOL_GITHUB_APP_HEAD_ACCOUNT`
   - `DEVTOOL_GITHUB_APP_HEAD_PRIVATE_KEY_PATH`

   On the head's own push, configure the primary installation to match its
   `origin`; for PR/CI use the upstream base installation as primary and the
   separate head installation for source reconciliation. The same App ID/private
   key can be reused in two separately installed accounts if authorized; their
   distinct installation IDs and selected repository access are always checked.

## Commands

Within an existing, qualified committed native delivery session, the parser
supports:

```
devtool delivery session push --id SESSION --auth app --yes
devtool delivery session pr --id SESSION --auth app --yes
devtool delivery session ci --id SESSION --auth app
```

Omit `--yes` to receive the read-only approval-required status. Existing
`auto`, `env`, `gh`, `none` provider selection on API requests and ordinary
SSH/PAT Git push behavior remain unchanged. A new `--auth app` on `push`
selects the verified one-operation HTTPS Git credential path; the normal
remote's URL remains unchanged. An SSH `origin` with `--auth app` is refused;
use explicit HTTPS origin set up by the operator or use the existing SSH mode.

`resolve_token('app', repository=..., capability=...)` is also available for
specialized API callers. Without an explicit repository, that standalone
resolver requires `DEVTOOL_GITHUB_APP_REPOSITORY` and fails closed if missing.

## Credential and CI semantics

- The private key is never persisted in session state. Authentication uses an
  in-memory RS256 JWT with short lifetime and independently verified API reads
  for App installation ID, account, repository membership, permissions and
  actual repository ID. Tokens are restricted to one repository and the minimum
  declared permissions; the GitHub API response is independently verified.
- Installation tokens are cached **in memory only**, refreshed before the
  five-minute safety window. GET polling can refresh credentials, but a failed
  or uncertain POST is **never** retried automatically.
- Native Git push uses a one-operation AskPass helper with a pipe-backed secret
  descriptor. The helper and directory are temporary/private, the token is not
  in Git arguments, URL, Git config or command-line process environment values.
  Stored credential helpers are overridden for that operation. Git push remains
  normal, non-force and individually journaled. The native remote SHA is
  independently re-checked before the intent is marked confirmed.
- App permissions are checked **before** a mutating native session intent; they
  do not grant mutation approval. PR remains draft by default.
- CI is only qualified by the existing required workflow policy, exact PR HEAD,
  actual workflow identities/event/run attempts/job conclusions and native
  observer receipts. A verified App token is **not** a CI or release pass.
- No token, private key, JWT or secret-bearing exception may be logged. Public
  receipt fields include App ID, installation ID, repository ID and capability.
  UI presentation of those metadata fields comes in M6, not in this milestone.

## Required acceptance not supplied by this source-only candidate

- Verify source pin, all touched blob hashes, exact overlay payload manifest,
  runtime identity and source tree on the actual Termux worktree.
- Run focused positive and negative tests with real Devtool `3.2.1+`, including
  Git credential helper injection, private-key substitution, wrong/missing
  installation, HEAD/BASE permissions, 401/403/404, API redirect and uncertain
  remote mutation. No security check may be disabled to pass.
- Only then native `check → apply → committed adoption`; preserve session ID.
- Approved push and draft PR; hosted checks verified at *actual* PR HEAD.
- Separately request the current 24-shard regression and verify all 24 shards
  and its fail-closed aggregate. Hosted CI does not imply device or release
  acceptance. No merge without explicit approval.

## Consolidated validator recovery compatibility

The M4 delivery also corrects the source-bound Guardian validator-child
receipt mismatch. The native event is `artifact-validate-script`; the
manifest configuration key remains `validation`. Both are checked without
accepting aliases or failed/skipped native child outcomes. This does not
reapply Guardian; recover its existing committed session only after the
Devtool change is merged and the trusted runtime is updated.
