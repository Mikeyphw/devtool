# FC-08 — Real-host, device, and consumer qualification (source implementation)

**Status: EVIDENCE_COLLECTION_IMPLEMENTED; EXTERNAL_GATE_OPEN.** FC-08 cannot be declared complete by an installed source overlay, by passing localhost pytest, or by the presence of a GitHub workflow. The original roadmap explicitly requires actual supported hosts, authenticated GitHub runs, real consumer repositories and applicable Android signing/installed evidence.

## Baseline and G1 carried forward

The FC-07 source regression inventory found 589 tracked modules and 6,737 test items. A large execution shard failed because Maven Surefire XML was discarded in favor of stdout. FC-08 captures pre-command report-file signatures and accepts a new/modified report by invocation identity rather than trusting an Android/Linux wall-clock timestamp. It still rejects an unchanged pre-existing report. Dedicated synthetic regressions test clock drift, stale reports, and same-path replacement.

The **complete G1 execution gate remains OPEN** until every sharded test run has a terminal zero exit code and an exact collection/JUnit receipt. The long shard tried during construction did not complete. This source-bound installer only uses a smaller, explicitly named validator, and must never convert that result into a claim about all 6,737 tests.

Use the existing manually triggered `.github/workflows/devtool-full-repository-pytest.yml` for its 24-shard, full-source CI gate. Preserve all artifacts, JUnit and the fail-closed final summary. It is not release/device certification.

## FC-08 lanes

| Lane ID | Minimum real qualification (separate from tool presence) | Initial status |
|---|---|---|
| `github-actions-dag` | Actual exported DAG, repeated/failed dispatch, artifacts/attempt and permission/access evidence on the reviewed commit | NOT_RUN |
| `windows-dotnet-msvc` | Actual Windows .NET shared restore, cold/warm store, TRX/coverage/publish bytes, MSVC CMake/native ABI | NOT_RUN |
| `linux-polyglot` | Actual Linux Python uv/Poetry/PDM, Go, Node, Rust, JVM, CMake/Meson, shell and negative lock/ABI cases | NOT_RUN |
| `macos-native` | Native admitted compiler/ABI and refused unsupported providers | NOT_RUN |
| `termux-arm64` | Native ARM64 CTest/Meson, owned temp and crash recovery, offline refusal, Termux operator CLI | NOT_RUN |
| `android-signing` | Real signed APK/AAB, installed provenance, instrumentation, authoritative signer/keystore chain | NOT_RUN |
| `consumer-adoption` | Real reviewed Telegram/Studyflow/BKX migration and cross-repository rollback/forward recovery | NOT_RUN |

All seven lanes remain **NOT_RUN** without corresponding evidence. The FC-08 manual hosted workflow performs *read-only tool/OS observation* and emits a **NOT QUALIFIED** report; it does not run the full workloads or grant any of these gates.

## Generate an immutable-source plan

```sh
python3 tools/fc08_evidence.py --output /tmp/fc08-plan.json plan --root "$PWD"
```

The report embeds `HEAD` and `HEAD^{tree}`. Keep it together with raw logs and source/tree references; do not copy results between commits.

## Hosted observation and API provenance

1. Push the **actual source commit** to the reviewed GitHub repository. A locally installed overlay or an unrelated historical Actions run is not sufficient.
2. Manually dispatch `devtool-fc08-host-evidence.yml` (read-only observations only). The separate full-Python workflow remains the G1 gate.
3. Save the real run ID, job ID, attempt, source SHA/tree, runner/OS, workflow path, and named artifact.
4. Query the authenticated GitHub API via:

```sh
GITHUB_TOKEN=... python3 tools/fc08_evidence.py --output /tmp/fc08-github.json github-verify \
  --repo owner/repo --run-id REAL_ID --job-id REAL_JOB_ID \
  --commit EXACT_40_CHAR_SHA --tree EXACT_40_CHAR_TREE --attempt REAL_ATTEMPT \
  --workflow-path .github/workflows/devtool-fc08-host-evidence.yml \
  --lane windows-dotnet-msvc --artifact-name fc08-windows-dotnet-msvc-attempt1
```

Never check tokens into source or logs. Failed API requests, incomplete job/artifact pages, wrong attempts, source drift, missing SHA-256 artifact digests, or unsupported runner labels fail closed. Even a successful metadata verification returns `HOSTED_METADATA_VERIFIED_ONLY`: the underlying artifact bytes, build/test content and release authority still require independent evaluation.

## Native Termux observation

Run on the actual device, using the installed Termux Python:

```sh
python3 tools/fc08_evidence.py --output "$HOME/fc08-termux-observation.json" device-probe --root "$HOME/Code/devtool"
```

This records the actual SDK/Clang/CMake/Meson availability; it does **not** execute CTest, device instrumentation, shell resilience or installed APK tests. Supply those separately as real device JSON receipts. Manual Skybind M0–M8 cases can remain deferred without being reported as failing automated tests.

## Real consumers

Use the current, exact consumer source snapshots and native Devtool source-bound proposals. Preserve each original HEAD/tree, approval digest, real target and workflow authority, local preflight, commit/recovery receipts, and post-change content hashes. Never overwrite custom CI or invent signing/hosting permission. If an inaccessible or private consumer cannot be independently reviewed, use `UNAVAILABLE` rather than a simulated `PASS`.

## Integrity and status rules

- A passing local pytest is `LOCAL_PASS`, not `HOSTED_PASS`.
- A hosted run entry without independent artifact digest is `UNVERIFIED`.
- GitHub API run/job/artifact metadata proves only authenticated **metadata**, not the receipt contents or test semantics.
- Android simulator, `ANDROID_ROOT` environment variables, Python monkeypatches and Linux desktop all fail to qualify a real native Termux or installed-signing lane.
- `NOT_RUN`, `UNAVAILABLE`, `REFUSED_UNSUPPORTED_HOST`, `FAILED` and `PASS` must be recorded literally for each attempted gate. An unexecuted gate is open, not failed.
- FC-09 final closure remains blocked until all applicable gates have verified external evidence or an explicit user-approved scope amendment.
