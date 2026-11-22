# M8 — finite signed-event continuation and PR maintenance

## Authority and deployment

This extends M8's `m8_webhooks.py` without substituting for GitHub App, native
`delivery session`, native Actions request journals, or B3 verification.
Webhook payloads are **wakeup signals only**. Successful HMAC, an App token,
or a passing GitHub check **never** authorizes a write or qualifies CI.

Run only from a worktree that contains the M8 modules and has a committed,
source-bound native delivery session. The receiver binds exclusively to
`127.0.0.1`; you must provide and configure your own trusted HTTPS gateway
for real GitHub deliveries. Never expose the loopback server directly.

## Opt-in policy: immutable, single-PR and at most seven days

First use `devtool.m8_webhooks pin` to create a private inbox with the true
repository/installation ID, PR number, commit SHA and session ID. Prepare a
JSON policy with **all** the following fields and your own actual values:

```json
{
  "schema": "devtool.m8.automation-policy.v1",
  "policy_id": "11111111-1111-4111-8111-111111111111",
  "repository": "OWNER/REPO", "repository_id": 1, "installation_id": 1,
  "head_sha": "0000000000000000000000000000000000000000",
  "head_branch": "feature/my-branch", "base_branch": "main",
  "base_sha": "0000000000000000000000000000000000000000",
  "pr_number": 1, "session_id": "00000000000000000000000000000000",
  "expires_at": "2026-11-25T00:00:00Z",
  "events": ["pull_request:synchronize", "workflow_run:completed"],
  "effects": ["observe"], "labels": [], "comment": "",
  "status_context": "", "status_state": "", "workflow": "",
  "rerun_run_id": 0, "max_events": 30
}
```

These are **illustrative placeholders**, not automatically usable credentials
or approval. Set the expiry within seven days from the current UTC time.
No event or network callback can create this approval. To approve manually:

```zsh
python3 -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" approve --policy "$HOME/.config/devtool/m8-policy.json" --auth app --yes
```

`approve` verifies the immutable webhook pin, local native session, repository
numeric identity, current remote branch and PR head/base before storing a
private, immutable receipt. The scope cannot later expand in place. Revocation
is a separate durable marker:

```zsh
python3 -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" revoke --yes
```

## Read-only follow and no-public-receiver fallback

Signed events from the existing private M8 inbox can be re-verified against
native B3 using the **original CI24 request ID**. Delivery UUIDs are journaled
once, including ignored policy-excluded events. Interrupted verification does
not mark a delivery processed. Repeated/out-of-order events always recheck
current source and native CI evidence, never trust event conclusions.

```zsh
python3 -u -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" follow --request-id YOUR_32_HEX_REQUEST_ID --checks 12 --interval 15
```

Termux deployments without an accessible webhook endpoint can use **bounded
read-only polling**. The process runs in the current terminal, not an unstated
background service:

```zsh
python3 -u -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" poll --request-id YOUR_32_HEX_REQUEST_ID --checks 12 --interval 30
```

This verifies live PR/head/base identity, rechecks native B3 receipts, and
keeps deduplicated, append-only snapshot changes in private state. A terminated
operator can rerun `poll` without modifying GitHub resources or losing existing
observations. Missing App grants/permissions and rate-limited providers fail
closed rather than silently switching to mutation.

## Separately authorized maintenance

Only a source-bound, manually approved policy containing an effect allows its
use. Even then, each operation requires a new real `--yes` invocation.
Examples, **not** blanket approvals:

```zsh
python3 -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" maintain --effect pr_labels_add --auth app --yes
python3 -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" maintain --effect pr_comment --auth app --yes
python3 -B -m devtool.m8_automation --state-dir "$HOME/.local/state/devtool/m8-webhooks" maintain --effect commit_status --request-id YOUR_32_HEX_REQUEST_ID --auth app --yes
```

Label and comment bodies and status context/state must be **fixed in the
approved policy**; an incoming webhook cannot choose them. Commit-status
`success` additionally requires a fresh passing native B3 receipt. The new
native delivery intent identities `pr_comment`, `pr_labels`, and
`commit_status` are recorded **before** sending a bounded GitHub POST, then
confirmed only after independent provider GET. Failed/ambiguous postimages
leave the native session unresolved and block retries pending reconciliation.

GitHub App grants are capability-scoped (`pr_maintain` and `status_write`).
Missing approval or installation permission blocks the operation and is not
a reason to broaden the App's privileges automatically.

Explicit CI dispatch/rerun is delegated to the canonical Devtool Actions CLI.
Only a pinned `devtool-full-repository-pytest.yml` workflow is eligible, and
rerun policies additionally bind the exact failed run ID. M8 records a separate
one-use intent before invocation. Missing/ambiguous native receipts stop; no
implicit replays or infinite reruns. No merge, force push, permission change,
release, or device action is implemented.

## Acceptance and limits

The focused hosted workflow exercises signed webhook HTTP intake in loopback
with a synthetic secret and adversarial replay/identity/expiry paths, approved
PR maintenance using a fake GitHub provider, and native intent/Actions adapter
contracts. It does **not** prove that a public TLS endpoint has been deployed,
that your real App has the required write permission, or that physical-device
or release qualification passed. Those need independent live, device and
release receipts. The existing full CI24 and B3 gates must be freshly
qualified for any new source commit. Do not reuse earlier PR #44 receipts.
