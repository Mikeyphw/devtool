# DN03-R2 / R06d — Exact runtime-only provisioning and real hosted native Devtool DAG

Source base: `Mikeyphw/devtool` merged DN03-R1 PR #4 at `e2fea8a784711496e026030ec5418514aec4bebf`.

**Boundary:** This is a consolidated source implementation plus a separately required *real hosted run*. It does not count source tests as native execution and does not grant release, signing, package-publisher or external identity authority.

## What this phase implements

1. **DN03 generated .NET workflows:** `devtool.actions_dn03_dotnet` continues to use RM-06's existing `compile_graph`, `workflow_yaml`, and `run_node`; it does not add a scheduler. It now creates an isolated runner-local `DOTNET_INSTALL_DIR` *before* the SDK setup step. The generated .NET SDK setup stays SHA-pinned and requests the reviewed exact SDK from `global.json`. For jobs with reviewed net6 opt-in it removes the old 6.0.428 SDK setup step and inserts the runtime-only installer **after immutable Devtool source acquisition** and before native execution.
2. **Runtime integrity:** `devtool.dn03_runtime_only` downloads only Microsoft's Linux x64 `dotnet-runtime-6.0.36-linux-x64.tar.gz` over HTTPS, verifies its exact 128-hex-character SHA-512 **before any extraction**, copies only files under `shared/Microsoft.NETCore.App/6.0.36` with bounded archive/file sizes, refuses path traversal, symlinks, duplicate entries and pre-existing targets, and emits a create-only receipt. Its digest is the value published in Microsoft's [`6.0.36-sha.txt`](https://builds.dotnet.microsoft.com/dotnet/checksums/6.0.36-sha.txt). This checks bytes, **not** an independent Microsoft signature or artifact attestation. The SDK remains `10.0.100` in this hosted gate.
3. **Generated host receipts:** DN03's hosted host check now runs after runtime installation. The host receipt validates and hashes the associated runtime-only receipt; upload-artifact receives both host and runtime evidence under the same node directory. It is run-bound local evidence, not authenticated GitHub provenance.
4. **R06d real native Devtool DAG:** `.github/workflows/devtool-dn03-r06d-hosted-native.yml` clones immutable `Mikeyphw/Mike_SVMods` commit `549d6cd0d08894dfac4950e362e437eb06010729`, provisions Python and SDK in an isolated hosted runner, installs the SHA512-verified .NET 6 runtime, and executes **Devtool's actual five-node `svmods/ci-hosted-dotnet` workflow**: source, pure restore, pure test, animal restore, animal test.
5. **Fail-closed acceptance:** `tools/dn03_r06d_acceptance.py` checks the exact Devtool PR/push head, consumer tracked source/lock digests, native job SDK, runtime-only receipt and host runtime, then requires the consumer's independent five-node evidence verifier, both passing real TRX results and matching plan/preflight/invoke SHA256s. Each run uploads an evidence bundle even on failure; only a completed acceptance JSON plus passing job warrants a pass statement.
6. **Regression:** Additional unit and adversarial archive tests; existing DN01/DN02/DN03/RM09/FC06 source tests are kept in the DN03 source qualification workflow.

## Scope is deliberately narrow

The Mike_SVMods R06b native workflow consists of Devtool-managed **`runner = "command"`** .NET jobs. It qualifies a real *native command-job DAG*, not yet RM-09's generated **per-node dotnet provider** workflow. For that final requirement, a future consumer PR must export the DN03 reviewed RM-06 workflow with a target-owned `runner = "dotnet"` configuration and prove that exported workflow's jobs and artifacts on GitHub. The generated exporter change in this phase is covered by source tests; it is not independently hosted-qualified merely because R06d succeeds.

Other remaining gates: actual Windows-only WPF/WinForms qualification, signed/attested remote provenance, cross-job source/attempt authentication, offline fully hashed Python dependencies, and released artifact authority. .NET 6 is end-of-support and must be an explicit compatibility opt-in.

## Apply and run

The overlay is a true schema-v2 Devtool artifact with `base_contract_mode = "exact-tree"`, complete checksum inventory and `apply.commit.enabled=true`. Application is clean-tree and SHA-locked; no manual `git commit` needed.

```zsh
cd "$HOME/Code/devtool"
git fetch origin main
git switch main
git merge --ff-only origin/main
test "$(git rev-parse HEAD)" = e2fea8a784711496e026030ec5418514aec4bebf
git switch -c feat/dn03-r2-runtime-only-native
ZIP=/storage/emulated/0/Download/DEVTOOL_DN03_R2_R06D_CONSOLIDATED.overlay.zip
devtool --repo "$PWD" overlay check "$ZIP"
devtool --repo "$PWD" --yes apply-overlay "$ZIP" --keep-artifact
git status --short
git log -1 --oneline
git push -u origin feat/dn03-r2-runtime-only-native
```

The PR and push events run both the **DN03 source qualification** and **R06d hosted native acceptance**. A real pass requires the hosted workflow to execute all five jobs, verify both TRX files, and upload the `dn03-r06d-native-...` artifact. **Don't merge based on the Python suite alone.**
