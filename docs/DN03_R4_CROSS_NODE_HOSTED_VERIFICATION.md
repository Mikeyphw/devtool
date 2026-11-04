# DN03-R4 — Generated RM-09 native DAG: cross-node hosted evidence review

**Pinned Devtool merged base:** `Mikeyphw/devtool` `main` at
`48d689aa7bab914ce31eb9ee1d185fc205ceed76` (merged PR #6), Git tree
`70038fcd73685a0238366496f078adae4e62b0f2`.

The source-pinned artifact uses schema-v2 **exact-tree** verification and adds
five new files. A changed HEAD or dirty checkout is refused before apply.
This artifact does not replace or rewrite the previously merged DN03-R3 code.

## Delivered engineering behavior

- `python3 -m devtool.actions_dn03_r4 plan|write|verify` extends the DN03-R3
  exporter. The existing RM-06 graph is the sole source of the native nodes,
  dependencies, commands, output declarations, host profiles and runtime pin.
  R4 appends **one observer job** named `dn03_r4_acceptance`; it does not
  replace, rewrite or reschedule any native .NET job.
- The observer `needs` **every** native node and uses `if: always()` so failure
  evidence is not silently skipped. A skipped, cancelled, absent or failed
  parent node is refused, even if a stale artifact exists.
- All GitHub Actions use immutable commit pins. The observer downloads
  each native typed evidence artifact and DN03 host receipt from the **same
  GitHub run/attempt**, plus the node's declared output bytes if present.
  No arbitrary wildcard artifact selector is used.
- A pinned source installation of Devtool recomputes the authoritative graph
  and complete generated YAML from the checked-out consumer source. It compares
  that YAML and the source-bound receipt to the **committed consumer files**.
- The verifier requires exact `GITHUB_SHA`, `GITHUB_REPOSITORY`, run ID,
  attempt, source tree, graph SHA-256 and Devtool commit; all nodes must share
  one immutable identity. It calls the existing typed-node verifier to rehash
  downloaded, declared native outputs and rejects altered/missing/symlinked
  receipts and node identity drift.
- R4 rechecks the R3 local verification receipt against downloaded native and
  host receipt SHA-256. It writes a create-only `acceptance.json` on PASS.
  Upload includes only a compact verdict, source/run breadcrumb and hash list;
  it does not upload duplicate native packages a second time.

## Consumer workflow creation

The **consumer** must already be eligible for RM-09: one fully configured,
tracked .NET-only target/workflow, tracked `global.json`, project references,
NuGet lockfiles and reviewable native commands. The public
`Mikeyphw/Mike_SVMods` workflow `svmods/ci-hosted-dotnet` at source
`549d6cd0d08894dfac4950e362e437eb06010729` contains a `ci-source`
command runner and therefore is **not** an eligible .NET-only R4 workflow.
That consumer needs a separately reviewed source configuration/overlay before
real R4 GitHub execution can begin.

After Devtool R4 is merged, on a clean, eligible *consumer* repository:

```bash
PIN="$(git -C "$HOME/Code/devtool" rev-parse HEAD)"
python3 -m devtool.actions_dn03_r4 plan \
  --repo "$PWD" --target TARGET --workflow WORKFLOW --devtool-sha "$PIN"
python3 -m devtool.actions_dn03_r4 write \
  --repo "$PWD" --target TARGET --workflow WORKFLOW --devtool-sha "$PIN" --yes
# Commit both generated files in the CONSUMER, then verify after commit.
python3 -m devtool.actions_dn03_r4 verify \
  --repo "$PWD" --target TARGET --workflow WORKFLOW --devtool-sha "$PIN"
```

The checked-in files are `.github/workflows/devtool-dn03r4-TARGET-WORKFLOW.yml`
and `docs/ci/devtool-dn03r4-TARGET-WORKFLOW.json`. Dispatch the generated
workflow in that consumer's GitHub repository, **not** in Devtool.

## Boundaries and remaining acceptance

1. **Source/contract tests do not qualify actual generated hosted execution.**
   The R4 verification job must pass in a real consumer run, against its native
   per-node artifacts. Prior R06d passed a different, five-node command-runner
   DAG and is not a substitute.
2. R4 correlates same-run downloaded bytes and existing node evidence; it does
   **not** independently authenticate GitHub's artifact producer or provenance
   signature. `remote_authenticated=false` and `release_qualified=false` remain
   mandatory in all receipts.
3. R4 never promotes an unverified xUnit/TRX result. Its
   `test_reports_independently_verified=false` is intentional. A consumer may
   qualify tests separately with the existing TRX parser/hosted evidence gate.
4. Installed SDK/Python packages may be dynamically resolved by the existing
   native workflow. Cross-node byte review is not an SDK/package supply-chain
   attestation. Windows-only workloads, distribution signing and release
   qualification are separate gates.

## Apply this Devtool overlay

This artifact adds five source files and auto-commits only after mandatory
validation. It modifies no existing path and requires an exact clean tree at
the merged PR #6 baseline. Use a dedicated feature branch:

```bash
cd "$HOME/Code/devtool"
test -z "$(git status --porcelain)"
git fetch origin main
git switch main
git merge --ff-only origin/main
test "$(git rev-parse HEAD)" = 48d689aa7bab914ce31eb9ee1d185fc205ceed76
git switch -c feat/dn03-r4-cross-node-provider
ZIP="$HOME/storage/downloads/DEVTOOL_DN03_R4_CROSS_NODE_CONSOLIDATED.overlay.zip"
devtool --repo "$PWD" overlay check "$ZIP"
devtool --repo "$PWD" --yes apply-overlay "$ZIP" --keep-artifact
# Review commit and status before pushing.
git log -1 --oneline
git status --short
```

**Automatic commit message:**
`feat(rm09): DN03-R4 cross-node hosted .NET evidence verification`.
The overlay validator exercises new R4 adversarial tests and prior
DN03/RM-09 regressions. Do not bypass it with `--no-validate`.
