# Original ACTIONS-09 — G5 polyglot CI and fan-in verification

## Status and boundaries

**Source implementation** for a reusable GitHub Actions workflow with a bounded two-platform/four-language matrix, source-bound cell receipts, immutable artifact ZIP verification and canonical Devtool registration. **No live GitHub Linux/Windows acceptance yet**; no real Rust/Windows toolchain execution was performed in the artifact-builder environment. The original promised generic arbitrary language projects and self-hosted metrics/releases/GitLab remain separate from the fixed-source demonstration fixtures shipped here.

Supported GitHub-hosted combinations: Python, Go, Rust and Java on `ubuntu-latest` and `windows-latest`. Every matrix cell executes the respective repository fixture with an actual toolchain and creates `receipt.json` only on a successful command. The matrix uses `fail-fast: false`, so a failing cell does not suppress unrelated results, but a failed/missing cell blocks entire fan-in. `workflow_dispatch` and `workflow_call` are both enabled.

## Operator flow (Termux)

The canonical provider is `devtool/github_actions.py`, not a second GitHub client. From a clean, committed repository after pushing its commit to GitHub:

```sh
devtool -r "$HOME/Code/devtool" actions ci plan --ref main --json
devtool -r "$HOME/Code/devtool" actions ci run --ref main --yes --json
# Once a run finishes, use exactly its run ID and the full source SHA:
devtool -r "$HOME/Code/devtool" actions ci check --run-id RUN_ID --expected-sha FULL_SHA --json
devtool -r "$HOME/Code/devtool" actions ci collect --run-id RUN_ID --expected-sha FULL_SHA --download --json
```

For a smaller smoke matrix pass `--languages python,go` and `--systems ubuntu-latest`; use the identical selections on plan/run/check/collect. The full matrix defaults to all eight cells. Dispatch sends an explicit GitHub request only with `--yes`; a 204 response with no run identity stays uncorrelated. Plan verifies the GitHub workflow's exact source commit matches the local clean committed checkout; mutable-ref changes between plan and POST remain uncertainty, never release evidence.

`ci check` is read-only and exits nonzero for an incomplete matrix. `ci collect` downloads through ACTIONS-04, verifies GitHub's archive SHA-256, ZIP safety/CRC, exact workflow and attempt, all expected jobs and all expected artifact receipts, and registers the original archives in the existing Devtool artifact registry. No archive member is executed. The full matrix succeeds only if **all requested language/OS jobs and artifacts** match the same exact source SHA and run attempt. A rerun attempt cannot inherit prior-attempt evidence.

## Trust and receipt schema

`tools/actions_g5_runner.py` runs a real language-specific fixture and emits a one-file JSON receipt with canonical language, OS, tool input matrix, GitHub repository, run/attempt/source SHA, fixture digest and passed/failed counts. The digest binds the fixture source checked out at GitHub's commit. The receipt is not itself a GitHub attestation; artifact origin comes from the existing GitHub API and canonical SHA-256 download verification. No claims of production releases: `qualified_release:false` and `source_attested:false` are invariant.

Signed or protected release workflows, ACTIONS-10 unsigned APK → local LIFE signing, G4 AAB lane and the successful native Termux Android debug baseline are unchanged. The private GitHub Free paid ruleset/governance gate is explicitly excluded from the mandatory implementation work, never forged or bypassed. G1 live negative tests, G2 crash/restart, G3 on-device/GTK, G4 live AAB, and the G5 live Windows/Go/Rust/JVM matrix remain OPEN until directly observed.

## Negative tests and next acceptance

Source tests reject unsupported/duplicate matrix parameters; dispatcher without `--yes`; wrong GitHub repository/workflow/source; missing, failed or duplicate jobs; jobs from a different attempt; expired/mismatched artifacts; tampered, unsafe or extra ZIP members; forged receipt source, attempt or matrix; missing source-checkout receipts; and partial fan-in. The planned live acceptance is a full 8-cell GitHub run with complete verified artifact fan-in and one retry/partial-failure scenario, using the existing CLI. Do not mark this milestone *live-qualified* solely from the local tests.
