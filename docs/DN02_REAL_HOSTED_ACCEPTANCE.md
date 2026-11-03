# DN02 — Real hosted .NET 6 acceptance (non-release)

Source baseline: `Mikeyphw/devtool` PR #3 head `d9dbb4c6010c00a8e67df1c4fae6430dc090fc32`.
Consumer: `Mikeyphw/Mike_SVMods` immutable commit `549d6cd0d08894dfac4950e362e437eb06010729`.

The workflow `.github/workflows/devtool-dn02-hosted-net6-acceptance.yml` runs automatically on the DN02 PR/push branch. It does **not** execute Gradle or .NET in Termux; all SDK installs/restores/tests run on GitHub's Ubuntu 24.04 hosted runner.

Acceptance uses `actions/checkout` and `actions/setup-dotnet` pinned to full immutable action commits. It requests .NET build SDK **10.0.100** and legacy SDK **6.0.428**, which supplies required runtime **6.0.36**. `tools/dn02_hosted_acceptance.py` invokes the actual DN02 host verifier to check the **exact** installed SDK and runtime from the consumer's pinned SDK directory.

The run checks out the immutable consumer revision, hashes tracked source and two project lockfiles, restores both test projects under `--locked-mode`, executes two real `net6.0` xUnit projects with `--no-restore` and emits separate VSTest TRX results. It calls the pinned consumer's real `scripts/verify_dotnet_trx.py` implementation to reject failed, skipped, incomplete or missing tests. A source/run/attempt/host-bound receipt and SHA256SUMS are uploaded even when a later step fails.

The `workflow_dispatch` trigger is usable once the workflow exists on the default branch; pushing to the DN02 feature branch or updating PR #3 triggers the acceptance before that point. Do not assert a hosted pass until the corresponding run and artifact are inspected.

## Safety limits

- .NET 6 is end-of-support; this is compatibility qualification, **not** security approval or release qualification.
- This evaluates the actual DN02 .NET host verification and real locked test execution. It is **not** yet an end-to-end RM-09 native provider graph execution. That remains an R06d gate.
- A test failure, missing lock, missing TRX, unexpected SDK/runtime or mismatched source/attempt fails closed.
- No privileges beyond `contents: read`, no secrets, no signing, no release permissions, no generated executable promoted to a release.
