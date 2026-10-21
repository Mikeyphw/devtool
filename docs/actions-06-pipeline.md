# ACTIONS-06 — Local/remote DAG coordinator

`actions pipeline` is a **bounded foreground coordinator**, not a new local workflow
scheduler. `local-invoke` delegates to Devtool's existing `invoke` DAG, while GitHub
nodes delegate to the ACTIONS-02 dispatch, ACTIONS-03 run observation, and
ACTIONS-04 SHA-256-verified artifact registry. No background process runs.

## Prepare the example for Devtool Live HUD

```sh
cd "$HOME/Code/devtool"
mkdir -p docs/ci
cp docs/templates/actions-pipeline-live-hud.json docs/ci/actions-pipeline.json
git add docs/ci/actions-pipeline.json && git commit -m 'ci: define Live HUD cross-boundary pipeline'
git push origin main

devtool -r "$PWD" actions pipeline plan --file docs/ci/actions-pipeline.json --json
devtool -r "$PWD" actions pipeline run --file docs/ci/actions-pipeline.json --yes --json
```

Copy the 32-character `pipeline_id` from the response; **do not dispatch again**.
When `remote_result` is `waiting`, use:

```sh
devtool -r "$HOME/Code/devtool" actions pipeline status --pipeline-id PIPELINE_ID --json
devtool -r "$HOME/Code/devtool" actions pipeline resume --pipeline-id PIPELINE_ID --yes --json
```

GitHub dispatch requires a matching remote `main` commit, a checked-in `workflow_dispatch`
workflow, and GitHub write permissions. A dispatch is not a completed build; remote
completion requires an exact run, workflow, event and commit match. Archive acquisition
requires the exact `artifact_name`, completed success, GitHub metadata and SHA-256 ZIP
verification. Archives are registered **unextracted and unqualified**; use
`actions android collect` separately for APK/AAB signature and manifest inspection.

## Manifest contract

- JSON schema `1`, at most 32 topologically ordered, uniquely named nodes.
- `needs` lists already declared direct dependencies. No arbitrary shell commands.
- `local-git`: asserts clean Git HEAD equals the pipeline's frozen source.
- `local-invoke`: runs a configured **existing** Devtool workflow action via the
  native `devtool invoke` engine; supports an optional `target`, timeout 900 seconds.
  Requires clean unmodified source afterward (excluding Devtool-owned `.devtool/private` and `.devtool/artifacts` runtime files, which the CLI itself may create). For example, add a node like
  `{"id":"check","kind":"local-invoke","action":"codegen","target":"devtool","needs":["source"]}`
  only when that action/target is declared and suitable in the target workspace.
- `github-dispatch`: workflow name, explicit `ref`, bounded nonsecret string `inputs`.
  Only explicit `--yes` authorizes sending a POST. A journal is durable before POST.
- `github-result`: `from` references a prior dispatch, included in `needs`; success
  is confirmed remotely, never inferred from dispatch acceptance. `waiting` means
  GitHub has not completed and can be resumed.
- `github-archive`: `from` references a dispatch, `needs` includes that dispatch
  and a successful result for the same dispatch, and `artifact_name` is exact.

Pipeline journal: `$XDG_STATE_HOME/devtool/actions/pipelines/<id>.json` (default
`~/.local/state/...`), written atomically, without token values, under restrictive
file permissions. Journal records a source SHA, JSON digest, repository, worktree,
request ID, run ID and per-node states. Resumption **never redispatches** nodes
in `running`, `uncertain`, `failure` or `blocked` states, including a crash between
GitHub POST and journal recording. Uncertain dispatches need manual reconciliation.

Only the ready subset runs on each foreground invocation. Independent branches are
executed sequentially by default; Devtool's existing native workflow scheduler owns
parallelism inside `local-invoke`. No releases are qualified by this coordinator.
Remote API outages do not imply success, and there is no automatic rerun, rollback,
retry of unconfirmed POSTs, or modification of GitHub Actions security permissions.
