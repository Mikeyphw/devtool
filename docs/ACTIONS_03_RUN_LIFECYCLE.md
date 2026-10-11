# ACTIONS-03 — GitHub Actions run observation, control and recovery

**Scope:** GitHub API workflow-run status and jobs, finite foreground polling, read-only repository access diagnostics, safe log ZIP retrieval, explicitly authorized cancellation/retry, and restart-safe observation bound to ACTIONS-02 receipts. This is **not** a background daemon, release gate, artifact verifier, or Android APK builder.

## Resolve GitHub access errors first

A 404 is deliberately ambiguous for privacy: GitHub uses it for missing and inaccessible resources. If Devtool reports a 404 from the `skybind` workspace, the selected Git origin might not point to the intended GitHub repository, the repository might be private, or the token might lack access. Check the local origin without printing any credentials:

```sh
git -C "$HOME/Code/skybind" remote -v
devtool -r "$HOME/Code/skybind" actions diagnose --github-repo OWNER/REPO --auth auto --json
devtool -r "$HOME/Code/skybind" actions workflows --github-repo OWNER/REPO --auth auto --json
```

Use `--github-repo OWNER/REPO` to bypass implicit Git origin selection, and `--auth none` only for public repositories. `--auth gh` uses the existing `gh auth login` session; `--auth env` uses GH_TOKEN/GITHUB_TOKEN, which should have Actions read permission (write for dispatch/cancellation/retry). Do **not** paste authentication tokens into the CLI or share them in issue reports.

## Remote run commands

```sh
devtool actions runs --github-repo OWNER/REPO --limit 20 --json
devtool actions view --github-repo OWNER/REPO --run-id 12345 --json
devtool actions jobs --github-repo OWNER/REPO --run-id 12345 --json
devtool actions watch --github-repo OWNER/REPO --run-id 12345 --max-checks 20 --interval 5 --json
# Correlate a prior ACTIONS-02 dispatch without re-resolving any Git remote:
devtool actions resume --request-id REQUEST_ID --json
devtool actions watch --request-id REQUEST_ID --max-checks 20 --interval 5 --json
# Save raw, unextracted ZIP of remote logs to Devtool private XDG state:
devtool actions logs --github-repo OWNER/REPO --run-id 12345 --json
# Explicit remote mutations; acceptance is NOT run completion:
devtool actions cancel --github-repo OWNER/REPO --run-id 12345 --yes --json
devtool actions rerun --github-repo OWNER/REPO --run-id 12345 --failed-only --yes --json
```

A run request ID is strictly bound to a recorded repository and GitHub run ID. ACTIONS-02 dispatches that returned legacy HTTP 204, or uncertain POST results, are **not** automatically correlated to a guessed latest run. Polling is finite and foreground only; rerun `resume`/`watch` after Termux exits. GitHub status is read fresh on each invocation. If the prior source commit doesn't match the remote run's `head_sha`, qualification is explicitly unconfirmed. A GitHub API outage does not convert the last observation into success.

Dispatch receipts remain under `${XDG_STATE_HOME:-~/.local/state}/devtool/actions/dispatches/` and are updated atomically with the last remote observation. Control intents are recorded **before** sending each non-idempotent POST under `actions/controls/`. Uncertain control outcomes are never automatically retried. These records contain only repository, run, event metadata—not credentials or workflow input values. Existing V3 asynchronous canonical log retention and V4 removal of Android phone log mirroring are preserved.

### Log download safety

GitHub issues a short-lived HTTPS redirect for run logs. Devtool does not forward the GitHub bearer token to that redirect. A bounded raw ZIP (32 MiB max) is saved with private file permissions and SHA-256, **without extraction**. Signed links are never persisted or displayed. Unsupported archive hosts fail closed. A downloaded log ZIP is *not* a qualified software build artifact.

## Current boundaries and unqualified work

ACTIONS-03 does not provide a background watch daemon, deduplicate GitHub-triggered jobs, auto-reconcile unknown/204 dispatches, synthesize missing evidence, or incorporate GitHub runs into Devtool's canonical DAG scheduler (ACTIONS-06). ACTIONS-04 artifact promotion, ACTIONS-05 Android APK support, and ACTIONS-08 signing/release gates remain separate. GitHub network integration is unverified in the offline test environment. Pre-existing V105 remediation findings remain open.
