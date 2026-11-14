# Devtool S3 — Committed required CI policy

The source-controlled `config/delivery-required-ci.json` file is the **only
required-check authority** used by `devtool delivery session ci` when present.
The S3 launcher resolves the same policy automatically and **never prompts for
workflow names**. An absent or altered policy is an error, not `OBSERVED_ONLY`.

## Policy contract

- `schema_version: 1`, `repository`, `base_branch`, `event: pull_request`
- `required_workflows`: 1–25 unique `{name, workflow_id}` entries
- `release_qualification: separate`: CI qualification is **not release signoff**

Every policy read compares the on-disk file to its exact bytes in Git `HEAD`.
The policy must be an ordinary tracked file, not a symlink. GitHub run identity
must match both the workflow name and numeric workflow ID, the session source
and the independently observed PR merge identity. A push-only run does not
satisfy a `pull_request` policy. A moved branch or policy blocks qualification.
The policy's SHA-256 is stored with the durable CI observation. Unknown, missing,
ambiguous, wrong-ID or failed checks are never treated as successful.

`--require-workflow` remains accepted for older scripts only when it lists
exactly the policy's entire set; omission automatically uses the policy.
There are no inferred or implicitly optional workflows.

## Commands

```sh
repo="$HOME/Code/devtool"
python3 -m devtool --repo "$repo" delivery session ci \
  --id SESSION_ID --checks 30 --interval 8
python3 -m devtool --repo "$repo" delivery session report --id SESSION_ID --json
```

The included Rich ZIP launcher wraps Devtool, persists beyond Devtool's exit,
checks this policy and its digest, and displays both **CI** and **release**
statuses separately. The resulting CI pass is *not* evidence of production
app/device validation or a release approval. It does not dispatch workflows.

## Drift and maintenance

Update the explicit workflow IDs/names through a reviewed Git commit and rerun
CI against a **new session bound to the new commit**. Never quietly amend an old
journal to a new required set. The file currently lists the six GitHub Actions
workflows verified on PR #17 at commit `df26a270a457092cc60ca33e0da2e46edf6756a2`.
No GitHub branch-protection rules were inferred from those observed runs.
