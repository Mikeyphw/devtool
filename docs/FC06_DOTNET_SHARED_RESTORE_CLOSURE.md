# FC-06 — RM09-A1 reviewed shared locked restore

## Source engineering contract

FC-06 introduces an optional, reviewed *same-job* multi-solution restore. In the metadata of a non-restore .NET native job, provide a list of 2–8 **tracked solutions** including the job's selected solution, e.g.:

```toml
[targets.app.jobs.build.metadata]
ci_dotnet_shared_solutions = ["App.sln", "Libraries.slnx"]
```

This is not a graph edge, a GitHub cache hit, or a claim that separate hosted jobs share `obj/`, project assets, or NuGet state. The compiler resolves tracked projects and their lockfiles, requires at least one overlapping locked NuGet package, rejects inconsistent hashes for the same package/version, binds the source/lock/SDK/host/framework/RID into an immutable review hash and places that review in the hosted graph. Different versions of the same ID have distinct NuGet store paths. Unsupported Windows-only selections on Linux are rejected before export.

At execution the native node checks the actual `dotnet --version`, revalidates the review against current tracked source bytes, creates a fresh private NuGet store, passes **the exact same `NUGET_PACKAGES` path** to each sequential `dotnet restore <solution> --locked-mode` and to the downstream `--no-restore` native job. Failed restores or unverified package metadata prevent downstream acceptance. Upon successful execution, every locked package's `.nupkg.sha512` metadata and independently streamed `.nupkg` archive SHA-512 are matched to the `contentHash` from tracked `packages.lock.json`, and the node produces `devtool.rm09-native-shared-restore-evidence` with review hash, SDK/lock identities, producer node, source commit, run/attempt and isolated-store identity hash. These are *local execution receipts*, not authenticated GitHub or NuGet signatures.

Legacy jobs without the reviewed metadata retain their conservative independent same-node locked restore. Such a job **does not** fulfill the literal RM09-A1 dependency sharing promise.

## Acceptance and boundaries

Unit and mocked integration tests include overlapping and conflicting package versions, one-store continuity across native restores and build, SDK mismatch, same-ID/version hash conflict, missing package metadata, symlinked cache artifacts, source/lock drift, Windows host refusal, fail-before-build and receipt honesty. The acceptance validator is deliberately SDK-independent for Termux. Real cold restore + warm/reuse against two actual solutions on a supported Linux/Windows .NET SDK runner **is pending external evidence**, along with actual TRX/coverage/publish receipts and package-fetch observations. A synthetically populated store or mocked runner never authorizes that gate.

No unreviewed global NuGet cache restore or cross-job artifact transfer is introduced. No release qualification is implied. RM19 remains cancelled.
