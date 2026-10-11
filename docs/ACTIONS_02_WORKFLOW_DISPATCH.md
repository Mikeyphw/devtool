# ACTIONS-02 — Workflow dispatch and run-ID correlation

## Boundary

This milestone adds **planning and explicit dispatch** on top of ACTIONS-00/01. It does not install a GitHub runner, track remote jobs to completion, create APKs, retrieve artifacts, cancel workflows or qualify release evidence. The local dispatch record is only a receipt; the remote run's status remains unknown until ACTIONS-03.

`devtool actions plan WORKFLOW --ref BRANCH_OR_TAG --github-repo OWNER/REPO [--input KEY=VALUE] [--inputs-json PATH] --json` reads repository metadata, workflow metadata, the selected ref commit, and the YAML at that commit, then validates declared `workflow_dispatch` inputs without POSTing. It is permitted for publicly readable repositories with `--auth none`.

`devtool actions run WORKFLOW --ref BRANCH_OR_TAG --github-repo OWNER/REPO --yes [--inputs-json PATH] --json` requires a GitHub credential with repository Actions **write** permission and sends exactly one dispatch POST. `--yes` is mandatory. Explicit file input is preferable for sensitive values, since shell argv arguments can leak to process listings. Do not pass secrets as workflow_dispatch inputs when they belong in GitHub Actions secrets.

## Safety and provenance

- Fixed HTTPS GitHub.com API origin, no redirects, bounded GET responses, strict owner/repo and workflow identity.
- Ref validated as branch or tag name. The selected ref resolves to a 40-hex commit SHA via the GitHub API **before** dispatch; this is an observation, not a lock against an external branch update. No SHA identity can be qualified without checking the later run's `head_sha` (ACTIONS-03).
- Fetches `.github/workflows/*.yml|*.yaml` through GitHub's contents endpoint **at the observed SHA**; parses it with PyYAML safe_load, and requires declared `workflow_dispatch` trigger. Input keys/types/options/required/default metadata are enforced. PyYAML is a declared runtime dependency (`PyYAML>=6`); missing dependency => fail closed.
- Before a non-idempotent POST, creates a durable, 0600 request journal at `${XDG_STATE_HOME:-~/.local/state}/devtool/actions/dispatches/<request_id>.json`; it stores metadata, never credentials or input values. Never automatically retries an uncertain dispatch.
- Sends `return_run_details=true`, supporting the GitHub 200 response with `workflow_run_id`. The returned API and HTML URLs are checked against the exact repository/run identity. A legacy 204 response is recorded as `accepted-uncorrelated` with **no invented run ID**. Malformed response or network loss is recorded as uncertain.
- Dispatch response acceptance **does not mean the workflow succeeded**. No automatic run completion/validation claims.

## Open gates

No live authenticated GitHub dispatch was performed in offline validation. ACTIONS-03 must provide run polling, failure/status evidence, SHA confirmation, cancellation and restart-safe tracking, including reconciliation of uncertain/uncorrelated requests without guessing.
