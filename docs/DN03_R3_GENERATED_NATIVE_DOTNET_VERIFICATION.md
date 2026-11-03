# DN03-R3 — generated RM-09 native-node post-execution verification

## Reviewed source

- Repository: `Mikeyphw/devtool`.
- Exact initial source: `abf6dc20dfdc68a9322e05b370750f098380b339` (merged PR #5).
- This is one native Devtool schema-v2 overlay with checksum inventory, exact Git source/tree pin, embedded validator and **manifest-controlled automatic commit**.

## What is implemented

1. `python3 -m devtool.actions_dn03_r3` extends the existing DN03-R2 .NET-only, Linux x64, source-bound RM-06 multi-job workflow exporter. It reuses `compile_dn03`, `compile_graph` and the native RM-06 execution command; it does **not** add a second scheduler.
2. For each generated native .NET job, a new required step runs *after* the actual native command, but *before* that job's byte handoff upload. The step runs **from the reviewed Devtool virtual environment**, not the system Python, preventing a repeat of R06d's venv import regression.
3. `devtool.actions_dn03_r3_verify` recompiles the original frozen RM-06 graph. It rejects an unknown node, mixed provider, changed graph/runtime/source, unsafe evidence paths, missing native receipt, or incorrect GitHub source/run/attempt context. It uses the existing `verify_node_evidence` implementation with native `rm06-output` staging to independently rehash all declared bytes.
4. The R3 verifier additionally matches the DN03 host receipt's SDK, runtime, source, node and run identity. For legacy .NET 6 nodes, the host receipt must include the SHA-256 of the verified runtime-install receipt.
5. A successful node emits create-only `rm06-evidence/JOB/dn03-r3-node-verification.json` alongside the existing typed native evidence, so the existing RM-06 evidence upload preserves it. This record states `remote_authenticated: false`, `release_qualified: false`, and `test_reports_independently_verified: false`.
6. Source and negative tests cover actual R2 hardening integration, generated YAML and Bash syntax, safe `plan`/`write`/`verify`, repeated verification after Git commit, byte/host/source/run drift, symlink refusal, wrong output path and trust-claim refusal. The dedicated GitHub Actions source workflow executes these tests alongside earlier DN/RM09 regressions.

## Generate in a configured, reviewed .NET-only consumer

This stage does **not** create or modify workflows in Mike_SVMods automatically. The consumer must be configured for RM-09's native `dotnet` provider, with a tracked root `global.json` pin, tracked NuGet lockfiles, a Linux x64 compatible **.NET-only** workflow, and reviewed source. R3's `--devtool-sha` must name the **immutable** Devtool revision used in hosted execution; use an exact commit hash, never `main`.

```sh
cd "$HOME/Code/consumer"
PIN="$(git -C "$HOME/Code/devtool" rev-parse HEAD)"
python3 -m devtool.actions_dn03_r3 plan \
  --repo "$PWD" --target TARGET --workflow WORKFLOW --devtool-sha "$PIN"
python3 -m devtool.actions_dn03_r3 write \
  --repo "$PWD" --target TARGET --workflow WORKFLOW \
  --devtool-sha "$PIN" --yes
python3 -m devtool.actions_dn03_r3 verify \
  --repo "$PWD" --target TARGET --workflow WORKFLOW --devtool-sha "$PIN"
```

`write` is create-only and requires a clean Git worktree; it does **not** commit or push the consumer. The output is `.github/workflows/devtool-dn03r3-TARGET-WORKFLOW.yml` and `docs/ci/devtool-dn03r3-TARGET-WORKFLOW.json`. Review, commit and push them in the **consumer** to exercise the generated workflow on GitHub Actions.

### Current Mike_SVMods qualification boundary

The already-proven `svmods/ci-hosted-dotnet` command-runner workflow in the pinned `Mike_SVMods` source contains a non-.NET `ci-source` node and uses a nested `global.json`, not a tracked root SDK pin. DN03-R3 **must refuse** to treat that workflow as an eligible .NET-only RM-09 consumer without a separately reviewed consumer configuration change. The existing five-node R06d hosted PASS cannot be reused as generated RM-09 provider acceptance.

## Status and next milestone

- **Local implementation and test gate**: included in this overlay. Its GitHub workflow only qualifies source-level behavior once CI passes.
- **Real GitHub-hosted generated provider workflow**: **NOT YET RUN**. A consumer commit with an eligible provider-only workflow is required. Do not claim cross-job native RM-09 acceptance until that consumer's real generated workflow has run successfully and its per-node evidence receipts have been inspected.
- **Artifact producer authenticity / signing / Windows execution / release**: not attested. Typed evidence and SHA-256/SHA-512 attest to local integrity, not an independent GitHub trust root.

## Overlay application

From a clean, exact Devtool Git HEAD at `abf6dc20dfdc68a9322e05b370750f098380b339`, run `devtool --repo "$PWD" overlay check ZIP` then `devtool --repo "$PWD" --yes apply-overlay ZIP --keep-artifact` on a dedicated feature branch. The manifest has `apply.commit.enabled=true` and will commit on successful validation; **do not create an extra manual commit** unless Devtool reports a validated-uncommitted state.
