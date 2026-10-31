# ANDROID-R32 — Devtool-only hosted provider and source admission

**Baseline:** `e479000725f4a5441455b96251ba7176cf47aa0d` (R31).  **Scope:** `devtool` source only; no modifications to xbsctl, dustui, MMRL, Relatório or Telegram.

## Source promises and acceptance boundaries

| Promise | Source status | Evidence / limitation |
|---|---|---|
| A09 nested Go module is selected by actual target-root / `go -C` | Implemented | `actions_adoption._selection` uses the existing tracked Go workspace reviewer; `setup-go` uses the selected module's `go.mod`; refuses multiple distinct SDK manifests per A09 workflow. R32 tests plus existing A09 host regression. |
| Read-only/writable-output contradiction fails early | Implemented | `workflow_config._load_jobs` and `workflow_scheduler.scope_for_graph_node` now reject `read_only=true` with either ordinary `outputs` or typed `output_scopes`. Does not attempt to prove that arbitrary source code has zero side effects. |
| Opt-in SHA-256-pinned source-only Python script jobs | Implemented (bounded) | `python3 scripts|tools|ci/<tracked>.py` with `metadata.ci_python_script_sha256`; direct argv only, optional args exactly equal to `ci_python_script_args`, no shell string, symlinks, untracked files or mutable source hash. The script remains executable code and must undergo independent code review. |
| Reviewed first-class hosted Android/Gradle provider | Implemented (bounded debug source) | New `android-gradle` built-in; exact `./gradlew` argv, explicit JDK/SDK pins, tracked wrapper+jar digest and Gradle distribution SHA, exactly one module-scoped `assembleDebug`, `bundleDebug`, `lintDebug`, or `testDebugUnitTest`. Native Devtool DAG and multi-job Actions exporter are used. **Only `runner="command"` (direct wrapper) is supported, not semantic `runner="android"`/`gradle_tasks`.** |
| GitHub runner provisioning | Generated, not executed | Multi-job YAML pins setup-java, setup-android, and setup-gradle to full immutable Git SHAs, and invokes `devtool.actions_graph_export run-node` with graph/source/run/attempt identity and bounded output transfer. No Gradle runs on Termux. Real GitHub hosted execution is **not yet demonstrated**. |
| Signing/device refusal | Implemented | Android provider has a strict debug task grammar; root/device/Termux/signing metadata is refused by existing provider admission. Generated workflow has read-only token permissions and no release secrets. No device/install/signature claims. |
| Android output ownership | Implemented, not real package verified | Debug build requires an exact single declared path under `module/build/outputs/apk/debug/*.apk` or `module/build/outputs/bundle/debug/*.aab`; RM-06 rejects tracked-source overwrites and symlink paths; stage produces SHA-256 byte evidence. Actual signing/package identity requires separate verification. |
| Unified provider/adoption diagnostics | Implemented | `actions provider-doctor` now distinguishes A09 source admission, provider plan, strict multi-job export, host execution **NOT_VERIFIED**, and release **NOT_QUALIFIED**. No remote API queries or promotion. |
| Existing R31 action lineage/dispatch and artifact registry | Unchanged | R32 reuses the existing flow and deliberately creates no second dispatcher/importer. Exact-run Android package provenance is **not** conferred by RM-06 byte-transfer receipts. |
| Real hosted Android build, verified APK/AAB signer, device installation, FC-09 release | OPEN | Requires future consumer configuration, real GitHub Actions execution and independent artifact/device evidence. Not claimed by R32. |

## Example of an explicitly reviewed consumer contract (illustrative only)

```toml
[targets.app.jobs.hosted_debug]
runner = "command"
command = ["./gradlew", "--no-daemon", ":app:assembleDebug"]
outputs = ["app/build/outputs/apk/debug/app-debug.apk"]
metadata = { ci_toolchain = "android-gradle", ci_android_java_version = "17", ci_android_sdk_packages = ["platform-tools", "platforms;android-35", "build-tools;35.0.0"], ci_android_wrapper_jar_sha256 = "<sha256 of tracked gradle/wrapper/gradle-wrapper.jar>" }
```

This requires tracked `settings.gradle(.kts)`, module `build.gradle(.kts)`, `gradlew`, `gradle-wrapper.jar`, and `gradle-wrapper.properties` with a valid official `distributionUrl` and `distributionSha256Sum`. This example is not a consumer patch and does not authorize running `assembleRelease` or instrumentation on GitHub.

Python source check example (also illustrative):

```toml
[targets.app.jobs.source_check]
runner = "command"
command = ["python3", "tools/source_check.py"]
metadata = { ci_toolchain = "python-shell", ci_python_script_sha256 = "<sha256 of tracked Python script>" }
```

## Source acceptance evidence

- Added tests: `tests/test_android_r32_hosted_provider.py`, `tests/test_r32_python_go_contracts.py`. Explicit hostile input coverage: release task, instrumentation, extra Gradle task, init script, unsafe output, missing distribution hash, symlinked wrapper, root/device/signing/Termux, source-tampered Python script, untracked script and nested Go.
- Existing regressions: `test_actions_a09_ci_host_contract.py`, `test_rm01_output_scopes.py`, `test_rm08_projects.py`, `test_rm08_completion.py`, `test_rm06_graph_export.py`, `test_actions_a03_adoption.py`, and the scheduler/EXO tests.
- R32 GitHub hosted source-qualification workflow is read-only and does **not** call Gradle, sign packages, import artifact authority, or qualify a release.
- Qualification must preserve one frozen source SHA per run; tests from an earlier SHA are not evidence for a later overlay.

## Later (not delivered in this Devtool-only milestone)

1. Hosted acceptance of a real consumer-declared debug DAG on GitHub; Android SDK and Gradle compatibility must be proven, not inferred.
2. Support reviewed `runner="gradle"/"android"` semantic jobs and nested Gradle project roots in the same native graph (currently refused rather than silently misrouting to Termux).
3. Exact-run artifact importer and R31 source/attempt/package/signature identity checks on real APK/AAB candidates, followed by device testing.
4. Optional enterprise host profiles, reproducible Android dependencies and parser-level shell review improvements without loosening signer/credential boundaries.
